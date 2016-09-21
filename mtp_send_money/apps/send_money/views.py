import datetime
import logging
import random

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import is_safe_url
from django.utils.translation import gettext, gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from mtp_common.email import send_email
from oauthlib.oauth2 import OAuth2Error
import requests
from requests.exceptions import Timeout as RequestsTimeout
from slumber.exceptions import SlumberHttpBaseException

from send_money import forms as send_money_forms
from send_money.models import PaymentMethod
from send_money.utils import (
    bank_transfer_reference, get_service_charge,
    get_api_client, get_link_by_rel, govuk_headers, govuk_url, site_url
)

logger = logging.getLogger('mtp')


def clear_session_view(request):
    """
    View that clears the session and restarts the user flow.
    @param request: the HTTP request
    """
    request.session.flush()
    return redirect('/')


def help_view(request):
    """
    FAQ section
    @param request: the HTTP request
    """
    context = {}
    return_to = request.META.get('HTTP_REFERER')
    if is_safe_url(url=return_to, host=request.get_host()):
        context['return_to'] = return_to
    return render(request, 'send_money/help.html', context=context)


class SendMoneyView(View):
    previous_view = None

    @classmethod
    def get_previous_views(cls, view):
        if view.previous_view:
            yield from cls.get_previous_views(view.previous_view)
            yield view.previous_view

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.valid_form_data = {}

    def build_view_url(self, url_name):
        url_name = '%s:%s' % (self.request.resolver_match.namespace, url_name)
        return reverse(url_name)

    def dispatch(self, request, *args, **kwargs):
        for view in self.get_previous_views(self):
            if not hasattr(view, 'form_class') or not view.is_form_enabled():
                continue
            form = view.form_class.unserialise_from_session(request)
            if form.is_valid():
                self.valid_form_data[view.url_name] = form.cleaned_data
            else:
                return redirect(self.build_view_url(view.url_name))
        return super().dispatch(request, *args, **kwargs)


class SendMoneyFormView(SendMoneyView, FormView):
    @classmethod
    def is_form_enabled(cls):
        return True

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        if self.request.method == 'GET':
            form = self.form_class.unserialise_from_session(self.request)
            if form.is_valid():
                # valid form found in session so restore it
                context_data['form'] = form
        return context_data

    def form_valid(self, form):
        # save valid form to session
        form.serialise_to_session(self.request)
        return super().form_valid(form)


class PaymentMethodChoiceView(SendMoneyFormView):
    url_name = 'choose_method'
    template_name = 'send_money/choose-method.html'
    form_class = send_money_forms.PaymentMethodChoiceForm
    experiment_cookie_name = 'EXP-first-payment-choice'
    experiment_variations = ['debit-card', 'bank-transfer']
    experiment_lifetime = datetime.timedelta(days=300)

    @classmethod
    def is_form_enabled(cls):
        return settings.SHOW_BANK_TRANSFER_OPTION and settings.SHOW_DEBIT_CARD_OPTION

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.show_bank_transfer_first = False
        self.set_experiment_cookie = None

    def dispatch(self, request, *args, **kwargs):
        if settings.SHOW_BANK_TRANSFER_OPTION and settings.SHOW_DEBIT_CARD_OPTION:
            response = super().dispatch(request, *args, **kwargs)
            if self.set_experiment_cookie is not None:
                response.set_cookie(self.experiment_cookie_name, self.set_experiment_cookie,
                                    expires=timezone.now() + self.experiment_lifetime)
            return response
        if settings.SHOW_BANK_TRANSFER_OPTION:
            return redirect(self.build_view_url(BankTransferWarningView.url_name))
        if settings.SHOW_DEBIT_CARD_OPTION:
            return redirect(self.build_view_url(DebitCardPrisonerDetailsView.url_name))
        return redirect('submit_ticket')

    def get_experiment(self):
        experiment = {
            'show_bank_transfer_first': self.show_bank_transfer_first,
        }
        if not settings.ENABLE_PAYMENT_CHOICE_EXPERIMENT:
            return experiment

        variation = self.request.COOKIES.get(self.experiment_cookie_name)
        if variation not in self.experiment_variations:
            variation = random.choice(self.experiment_variations)
            self.set_experiment_cookie = variation
            context = 'pageview,/_experiments/display-payment-methods/%s/' % variation
        else:
            context = 'pageview,/_experiments/redisplay-payment-methods/%s/' % variation
        self.show_bank_transfer_first = variation == 'bank-transfer'

        experiment.update({
            'show_bank_transfer_first': self.show_bank_transfer_first,
            'context': context,
        })
        return experiment

    def get_context_data(self, **kwargs):
        experiment = self.get_experiment()
        context_data = super().get_context_data(**kwargs)
        context_data.update({
            'experiment': experiment,
            'service_charged': settings.SERVICE_CHARGED,
            'service_charge_percentage': settings.SERVICE_CHARGE_PERCENTAGE,
            'service_charge_fixed': settings.SERVICE_CHARGE_FIXED,
        })
        return context_data

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['show_bank_transfer_first'] = self.show_bank_transfer_first
        return kwargs

    def form_valid(self, form):
        if form.cleaned_data['payment_method'] == PaymentMethod.bank_transfer.name:
            self.success_url = self.build_view_url(BankTransferWarningView.url_name)
        else:
            self.success_url = self.build_view_url(DebitCardPrisonerDetailsView.url_name)
        return super().form_valid(form)


# BANK TRANSFER FLOW


class BankTransferFlow(SendMoneyView):
    def dispatch(self, request, *args, **kwargs):
        if not settings.SHOW_BANK_TRANSFER_OPTION:
            raise Http404('Bank transfers are not available')
        return super().dispatch(request, *args, **kwargs)


class BankTransferWarningView(BankTransferFlow, TemplateView):
    url_name = 'bank_transfer_warning'
    previous_view = PaymentMethodChoiceView
    template_name = 'send_money/bank-transfer-warning.html'

    def get_success_url(self):
        return self.build_view_url(BankTransferPrisonerDetailsView.url_name)


class BankTransferPrisonerDetailsView(BankTransferFlow, SendMoneyFormView):
    url_name = 'prisoner_details_bank'
    previous_view = BankTransferWarningView
    template_name = 'send_money/bank-transfer-form.html'
    form_class = send_money_forms.BankTransferPrisonerDetailsForm

    def get_success_url(self):
        return self.build_view_url(BankTransferReferenceView.url_name)


class BankTransferReferenceView(BankTransferFlow, TemplateView):
    url_name = 'bank_transfer'
    previous_view = BankTransferPrisonerDetailsView
    template_name = 'send_money/bank-transfer.html'

    def get(self, request, *args, **kwargs):
        prisoner_details = self.valid_form_data[BankTransferPrisonerDetailsView.url_name]
        kwargs.update({
            'account_number': settings.NOMS_HOLDING_ACCOUNT_NUMBER,
            'sort_code': settings.NOMS_HOLDING_ACCOUNT_SORT_CODE,
            'bank_transfer_reference': bank_transfer_reference(
                prisoner_details['prisoner_number'],
                prisoner_details['prisoner_dob'],
            )
        })
        response = super().get(request, *args, **kwargs)
        request.session.flush()
        return response


# DEBIT CARD FLOW


class DebitCardFlowException(Exception):
    pass


class DebitCardFlow(SendMoneyView):
    def dispatch(self, request, *args, **kwargs):
        if not settings.SHOW_DEBIT_CARD_OPTION:
            raise Http404('Debit cards are not available')
        return super().dispatch(request, *args, **kwargs)


class DebitCardPrisonerDetailsView(DebitCardFlow, SendMoneyFormView):
    url_name = 'prisoner_details_debit'
    previous_view = PaymentMethodChoiceView
    template_name = 'send_money/debit-card-form.html'
    form_class = send_money_forms.DebitCardPrisonerDetailsForm

    def get_success_url(self):
        return self.build_view_url(DebitCardAmountView.url_name)


class DebitCardAmountView(DebitCardFlow, SendMoneyFormView):
    url_name = 'send_money_debit'
    previous_view = DebitCardPrisonerDetailsView
    template_name = 'send_money/send-money.html'
    form_class = send_money_forms.DebitCardAmountForm

    def get_context_data(self, **kwargs):
        kwargs.update({
            'service_charged': settings.SERVICE_CHARGED,
            'service_charge_percentage': settings.SERVICE_CHARGE_PERCENTAGE,
            'service_charge_fixed': settings.SERVICE_CHARGE_FIXED,
            'sample_amount': 20,  # in pounds
        })
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return self.build_view_url(DebitCardCheckView.url_name)


class DebitCardCheckView(DebitCardFlow, TemplateView):
    url_name = 'check_details'
    previous_view = DebitCardAmountView
    template_name = 'send_money/check-details.html'

    def get_context_data(self, **kwargs):
        prisoner_details = self.valid_form_data[DebitCardPrisonerDetailsView.url_name]
        amount_details = self.valid_form_data[DebitCardAmountView.url_name]
        kwargs.update(**prisoner_details)
        kwargs.update(**amount_details)
        return super().get_context_data(service_charged=settings.SERVICE_CHARGED, **kwargs)

    def get_prisoner_details_url(self):
        return self.build_view_url(DebitCardPrisonerDetailsView.url_name)

    def get_amount_url(self):
        return self.build_view_url(DebitCardAmountView.url_name)

    def get_success_url(self):
        return self.build_view_url(DebitCardPaymentView.url_name)


class DebitCardPaymentView(DebitCardFlow):
    url_name = 'debit_card'
    previous_view = DebitCardCheckView

    def get(self, request):
        prisoner_details = self.valid_form_data[DebitCardPrisonerDetailsView.url_name]
        amount_details = self.valid_form_data[DebitCardAmountView.url_name]

        amount_pence = int(amount_details['amount'] * 100)
        service_charge_pence = int(get_service_charge(amount_details['amount']) * 100)
        payment_ref = None
        failure_context = {
            'short_payment_ref': _('Not known')
        }
        try:
            client = get_api_client()
            new_payment = {
                'amount': amount_pence,
                'service_charge': service_charge_pence,
                'recipient_name': prisoner_details['prisoner_name'],
                'prisoner_number': prisoner_details['prisoner_number'],
                'prisoner_dob': prisoner_details['prisoner_dob'].isoformat(),
            }
            api_response = client.payments.post(new_payment)
            payment_ref = api_response['uuid']
            failure_context['short_payment_ref'] = payment_ref[:8]

            new_govuk_payment = {
                'amount': amount_pence + service_charge_pence,
                'reference': payment_ref,
                'description': gettext('To this prisoner: %(prisoner_number)s' % prisoner_details),
                'return_url': site_url(
                    self.build_view_url(DebitCardConfirmationView.url_name) + '?payment_ref=' + payment_ref
                ),
            }
            govuk_response = requests.post(
                govuk_url('/payments'), headers=govuk_headers(),
                json=new_govuk_payment, timeout=15
            )

            try:
                if govuk_response.status_code != 201:
                    raise ValueError('Status code not 201')
                govuk_data = govuk_response.json()
                update_payment = {
                    'processor_id': govuk_data['payment_id']
                }
                client.payments(payment_ref).patch(update_payment)
                return redirect(get_link_by_rel(govuk_data, 'next_url')['href'])
            except (KeyError, ValueError):
                logger.exception(
                    'Failed to create new GOV.UK payment for MTP payment %s. Received: %s'
                    % (payment_ref, govuk_response.content)
                )
        except OAuth2Error:
            logger.exception('Authentication error')
        except SlumberHttpBaseException:
            logger.exception('Failed to create new payment')
        except RequestsTimeout:
            logger.exception(
                'GOV.UK Pay payment initiation timed out for %s' % payment_ref
            )

        return render(request, 'send_money/failure.html', failure_context)


class DebitCardConfirmationView(DebitCardFlow, TemplateView):
    url_name = 'confirmation'
    previous_view = DebitCardPaymentView

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.success = False

    def get_template_names(self):
        if self.success:
            return ['send_money/confirmation.html']
        return ['send_money/failure.html']

    def check_payment(self, payment_ref, context_data):
        # NB: can bail out and clear session by raising DebitCardFlowException,
        # otherwise simply sets self.success to True if it worked

        context_data.update({
            'short_payment_ref': payment_ref[:8],
        })
        try:
            client = get_api_client()
            api_response = client.payments(payment_ref).get()
            if api_response['status'] != 'pending':
                raise DebitCardFlowException

            context_data.update({
                'prisoner_name': api_response['recipient_name'],
                'amount': api_response['amount'] / 100,
            })

            govuk_id = api_response['processor_id']
            govuk_response = requests.get(
                govuk_url('/payments/%s' % govuk_id), headers=govuk_headers(),
                timeout=15
            )

            try:
                if govuk_response.status_code != 200:
                    raise ValueError('Status code not 200')
                govuk_data = govuk_response.json()
                if govuk_data['state']['status'] != 'success':
                    raise ValueError('Status message is not "success"')

                email = govuk_data.get('email')
                payment_update = {
                    'status': 'taken'
                }
                if email:
                    payment_update['email'] = email
                client.payments(payment_ref).patch(payment_update)

                self.success = True
                if email:
                    send_email(
                        email, 'send_money/email/confirmation.txt',
                        _('Send money to a prisoner: your payment was successful'),
                        context=context_data, html_template='send_money/email/confirmation.html'
                    )
            except (KeyError, ValueError):
                logger.exception(
                    'Failed to retrieve payment status from GOV.UK for payment %s. Received: %s'
                    % (payment_ref, govuk_response.content)
                )
        except OAuth2Error:
            logger.exception('Authentication error')
        except SlumberHttpBaseException:
            logger.exception(
                'Failed to access payment %s' % payment_ref
            )
        except RequestsTimeout:
            logger.exception(
                'GOV.UK Pay payment status update timed out for %s' % payment_ref
            )

    def get(self, request, *args, **kwargs):
        try:
            payment_ref = self.request.GET.get('payment_ref')
            if not payment_ref:
                raise DebitCardFlowException

            context_data = self.get_context_data(**kwargs)

            self.check_payment(payment_ref, context_data)

            response = self.render_to_response(context_data)
            request.session.flush()
            return response
        except DebitCardFlowException:
            return clear_session_view(request)
