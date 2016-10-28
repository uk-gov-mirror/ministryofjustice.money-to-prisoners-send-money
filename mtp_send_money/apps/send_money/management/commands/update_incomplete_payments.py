from decimal import Decimal
import logging

from django.core.management import BaseCommand
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException, Timeout as RequestsTimeout
from slumber.exceptions import SlumberHttpBaseException

from send_money.exceptions import GovUkPaymentStatusException
from send_money.payments import PaymentClient

logger = logging.getLogger('mtp')


class Command(BaseCommand):

    def handle(self, *args, **options):
        payment_client = PaymentClient()
        payments = payment_client.get_incomplete_payments()
        for payment in payments:
            payment_ref = payment['uuid']
            govuk_id = payment['processor_id']
            context = {
                'short_payment_ref': payment_ref[:8].upper(),
                'prisoner_name': payment['recipient_name'],
                'amount': Decimal(payment['amount']) / 100,
            }

            try:
                payment_client.check_govuk_payment_status(
                    payment_ref, govuk_id, context
                )
            except OAuth2Error:
                logger.exception(
                    'Scheduled job: Authentication error while processing %s' % payment_ref
                )
            except SlumberHttpBaseException as error:
                error_message = 'Scheduled job: Error while processing %s' % payment_ref
                if hasattr(error, 'content'):
                    error_message += '\nReceived: %s' % error.content
                logger.exception(error_message)
            except RequestsTimeout:
                logger.exception(
                    'Scheduled job: GOV.UK Pay payment check timed out for %s' % payment_ref
                )
            except RequestException as error:
                error_message = 'Scheduled job: GOV.UK Pay payment check failed for %s' % payment_ref
                if hasattr(error, 'response') and hasattr(error.response, 'content'):
                    error_message += '\nReceived: %s' % error.response.content
                logger.exception(error_message)
            except GovUkPaymentStatusException:
                # expected much of the time
                pass
