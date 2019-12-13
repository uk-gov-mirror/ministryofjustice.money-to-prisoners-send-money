from datetime import datetime
import json
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.test.testcases import SimpleTestCase
from django.utils.timezone import utc
import responses

from send_money.payments import CheckResult
from send_money.tests import mock_auth
from send_money.utils import api_url, govuk_url


@override_settings(GOVUK_PAY_URL='https://pay.gov.local/v1')
class UpdateIncompletePaymentsTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.mocked_is_first_instance = mock.patch(
            'send_money.management.commands.update_incomplete_payments.is_first_instance',
            return_value=True
        )
        self.mocked_is_first_instance.start()

    def tearDown(self):
        self.mocked_is_first_instance.stop()
        super().tearDown()

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def test_update_incomplete_payments(self):
        """
        Test that incomplete payments get updated appropriately.

        - wargle-1111 relates to a GOV.UK payment in 'success' status so
            * should become 'taken'
            * a confirmation email should be sent
        - wargle-2222 relates to a GOV.UK payment in 'submitted' status so should be ignored
        - wargle-3333 relates to a GOV.UK payment in 'failed' status without being in capturable status in the past
            so should become 'failed'
        - wargle-4444 relates to a GOV.UK payment in 'cancelled' status so:
            * should become 'rejected'
            * a notification email should be sent to the sender
        - wargle-5555 relates to a GOV.UK payment in 'failed' status which was in a capturable status in the past so:
            * should become 'expired'
            * a notification email should be sent to the sender
        - wargle-6666 relates to a GOV.UK payment in 'success' status after its credit was accepted by FIU so:
            * should become 'taken'
            * a different confirmation email should be sent
        - wargle-7777 relates to a GOV.UK payment in 'failed' status which was in a capturable status in the past and
            was rejected by FIU so:
            * should become 'expired'
            * a rejection email (not a timeout email) should be sent to the sender
        """
        payments = [
            {
                'uuid': 'wargle-1111',
                'processor_id': 1,
                'recipient_name': 'John',
                'amount': 1700,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A1409AE',
                'prisoner_dob': '1989-01-21',
            },
            {
                'uuid': 'wargle-2222',
                'processor_id': 2,
                'recipient_name': 'Tom',
                'amount': 2000,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A1234GJ',
                'prisoner_dob': '1954-04-17',
            },
            {
                'uuid': 'wargle-3333',
                'processor_id': 3,
                'recipient_name': 'Harry',
                'amount': 500,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A5544CD',
                'prisoner_dob': '1992-12-05',
            },
            {
                'uuid': 'wargle-4444',
                'processor_id': 4,
                'recipient_name': 'Lisa',
                'amount': 600,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A4444DB',
                'prisoner_dob': '1992-12-05',
            },
            {
                'uuid': 'wargle-5555',
                'processor_id': 5,
                'recipient_name': 'Tom',
                'amount': 700,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A4444DE',
                'prisoner_dob': '1992-12-05',
            },
            {
                'uuid': 'wargle-6666',
                'processor_id': 6,
                'recipient_name': 'Tim',
                'amount': 800,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A1409AW',
                'prisoner_dob': '1989-01-21',
                'security_check': {
                    'status': 'accepted',
                    'user_actioned': True,
                },
            },
            {
                'uuid': 'wargle-7777',
                'processor_id': 7,
                'recipient_name': 'Jim',
                'amount': 900,
                'status': 'pending',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A4444DQ',
                'prisoner_dob': '1992-12-05',
                'security_check': {
                    'status': 'rejected',
                    'user_actioned': True,
                },
            },
        ]
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json={
                    'count': len(payments),
                    'results': payments,
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 1),
                json={
                    'reference': 'wargle-1111',
                    'state': {'status': 'success'},
                    'settlement_summary': {
                        'capture_submit_time': '2016-10-27T15:11:05Z',
                        'captured_date': '2016-10-27'
                    },
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )
            # save email
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-1111'),
                json={
                    **payments[0],
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-1111'),
                json={
                    **payments[0],
                    'email': 'success_sender@outside.local',
                    'status': 'taken',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 2),
                json={
                    'reference': 'wargle-2222',
                    'state': {'status': 'submitted'},
                    'settlement_summary': {
                        'capture_submit_time': '2016-10-27T15:11:05Z',
                        'captured_date': None
                    },
                    'email': 'pending_sender@outside.local',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 3),
                json={
                    'reference': 'wargle-3333',
                    'state': {'status': 'failed'},
                    'settlement_summary': {
                        'capture_submit_time': None,
                        'captured_date': None
                    },
                    'email': 'failed_sender@outside.local',
                },
                status=200,
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-3333'),
                json={
                    **payments[2],
                    'status': 'failed',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 4),
                json={
                    'reference': 'wargle-4444',
                    'state': {'status': 'cancelled'},
                    'email': 'cancelled_sender@outside.local',
                },
                status=200,
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-4444'),
                json={
                    **payments[3],
                    'status': 'rejected',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 5),
                json={
                    'payment_id': 'wargle-5555',
                    'reference': 'wargle-5555',
                    'state': {
                        'status': 'failed',
                        'code': 'P0020',
                    },
                    'email': 'timedout_sender@outside.local',
                },
                status=200,
            )
            # get govuk payment events
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/events/' % 'wargle-5555'),
                status=200,
                json={
                    'events': [
                        {
                            'payment_id': 'wargle-5555',
                            'state': {
                                'status': 'capturable',
                                'finished': False,
                            },
                        },
                    ],
                    'payment_id': 'wargle-5555',
                },
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-5555'),
                json={
                    **payments[4],
                    'status': 'expired',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 6),
                json={
                    'reference': 'wargle-6666',
                    'state': {'status': 'success'},
                    'settlement_summary': {
                        'capture_submit_time': '2016-10-27T15:11:05Z',
                        'captured_date': '2016-10-27'
                    },
                    'email': 'success_after_delay_sender@outside.local',
                },
                status=200,
            )
            # save email
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-6666'),
                json={
                    **payments[5],
                    'email': 'success_after_delay@outside.local',
                },
                status=200,
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-6666'),
                json={
                    **payments[5],
                    'status': 'taken',
                },
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 7),
                json={
                    'payment_id': 'wargle-7777',
                    'reference': 'wargle-7777',
                    'state': {
                        'status': 'failed',
                        'code': 'P0020',
                    },
                    'email': 'timedout_sender@outside.local',
                },
                status=200,
            )
            # get govuk payment events
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/events/' % 'wargle-7777'),
                status=200,
                json={
                    'events': [
                        {
                            'payment_id': 'wargle-7777',
                            'state': {
                                'status': 'capturable',
                                'finished': False,
                            },
                        },
                    ],
                    'payment_id': 'wargle-7777',
                },
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-7777'),
                json={
                    **payments[0],
                    'status': 'expired',
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            # check wargle-1111
            self.assertEqual(
                json.loads(rsps.calls[3].request.body.decode()),
                {'email': 'success_sender@outside.local'},
            )
            self.assertDictEqual(
                json.loads(rsps.calls[4].request.body.decode()),
                {
                    'status': 'taken',
                    'received_at': '2016-10-27T15:11:05+00:00',
                },
            )
            self.assertEqual(
                mail.outbox[0].subject,
                'Send money to someone in prison: your payment was successful',
            )
            self.assertTrue('John' in mail.outbox[0].body)
            self.assertTrue('£17' in mail.outbox[0].body)

            # check wargle-3333
            self.assertEqual(
                json.loads(rsps.calls[7].request.body.decode()),
                {
                    'email': 'failed_sender@outside.local',
                    'status': 'failed',
                },
            )

            # check wargle-4444
            self.assertEqual(
                json.loads(rsps.calls[9].request.body.decode()),
                {
                    'email': 'cancelled_sender@outside.local',
                    'status': 'rejected',
                },
            )
            self.assertEqual(
                mail.outbox[1].subject,
                'Send money to someone in prison: your payment has NOT been sent to the prisoner',
            )
            self.assertTrue('Lisa' in mail.outbox[1].body)
            self.assertTrue('£6' in mail.outbox[1].body)

            # check wargle-5555
            self.assertEqual(
                json.loads(rsps.calls[12].request.body.decode()),
                {
                    'email': 'timedout_sender@outside.local',
                    'status': 'expired',
                },
            )
            self.assertEqual(
                mail.outbox[2].subject,
                'Send money to someone in prison: payment session expired',
            )
            self.assertTrue('Tom' in mail.outbox[2].body)
            self.assertTrue('£7' in mail.outbox[2].body)

            # check wargle-6666
            self.assertEqual(
                json.loads(rsps.calls[14].request.body.decode()),
                {'email': 'success_after_delay_sender@outside.local'},
            )
            self.assertDictEqual(
                json.loads(rsps.calls[15].request.body.decode()),
                {
                    'status': 'taken',
                    'received_at': '2016-10-27T15:11:05+00:00',
                },
            )
            self.assertEqual(
                mail.outbox[3].subject,
                'Send money to someone in prison: your payment has now gone through',
            )
            self.assertTrue('Tim' in mail.outbox[3].body)
            self.assertTrue('£8' in mail.outbox[3].body)

            # check wargle-7777
            self.assertEqual(
                json.loads(rsps.calls[18].request.body.decode()),
                {
                    'email': 'timedout_sender@outside.local',
                    'status': 'expired',
                },
            )
            self.assertEqual(
                mail.outbox[4].subject,
                'Send money to someone in prison: your payment has NOT been sent to the prisoner',
            )
            self.assertTrue('Jim' in mail.outbox[4].body)
            self.assertTrue('£9' in mail.outbox[4].body)

            # double-check that no more emails were sent
            self.assertEqual(len(mail.outbox), 5)

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def test_update_incomplete_payments_extracts_card_details(self):
        """
        Test that card details are extracted from the GOV.UK payment and saved on the MTP payment.
        """
        payment = {
            'uuid': 'wargle-1111',
            'processor_id': 1,
            'recipient_name': 'John',
            'amount': 1700,
            'status': 'pending',
            'modified': datetime.now().isoformat() + 'Z',
            'prisoner_number': 'A1409AE',
            'prisoner_dob': '1989-01-21',
        }
        payment_extra_details = {
            'cardholder_name': 'Jack Halls',
            'card_brand': 'Visa',
            'worldpay_id': '11112222-1111-2222-3333-111122223333',
            'email': 'success_sender@outside.local',
            'card_number_first_digits': '100002',
            'card_number_last_digits': '1111',
            'card_expiry_date': '11/18',
            'billing_address': {
                'line1': '102 Petty France',
                'line2': '',
                'city': 'London',
                'postcode': 'SW1H9AJ',
                'country': 'GB',
            },
        }
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json={
                    'count': 1,
                    'results': [payment],
                },
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % 1),
                json={
                    'reference': 'wargle-1111',
                    'state': {'status': 'success'},
                    'settlement_summary': {
                        'capture_submit_time': '2016-10-27T15:11:05Z',
                        'captured_date': '2016-10-27'
                    },
                    'card_details': {
                        'card_brand': 'Visa',
                        'last_digits_card_number': '1111',
                        'first_digits_card_number': '100002',
                        'cardholder_name': 'Jack Halls',
                        'expiry_date': '11/18',
                        'billing_address': {
                            'line1': '102 Petty France',
                            'line2': '',
                            'postcode': 'SW1H9AJ',
                            'city': 'London',
                            'country': 'GB'
                        },
                    },
                    'provider_id': '11112222-1111-2222-3333-111122223333',
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-1111'),
                json={
                    **payment,
                    **payment_extra_details,
                },
                status=200,
            )
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % 'wargle-1111'),
                json={
                    **payment,
                    **payment_extra_details,
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            self.assertDictEqual(
                json.loads(rsps.calls[-2].request.body.decode()),
                payment_extra_details,
            )
            self.assertDictEqual(
                json.loads(rsps.calls[-1].request.body.decode()),
                {
                    'received_at': '2016-10-27T15:11:05+00:00',
                    'status': 'taken',
                }
            )

    ref = 'wargle-1111'
    processor_id = '1'
    payment_data = {
        'count': 1,
        'results': [
            {
                'uuid': ref,
                'processor_id': processor_id,
                'recipient_name': 'John',
                'amount': 1700,
                'status': 'pending',
                'email': 'success_sender@outside.local',
                'modified': datetime.now().isoformat() + 'Z',
                'prisoner_number': 'A1409AE',
                'prisoner_dob': '1989-01-21'
            },
        ],
    }

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def test_update_incomplete_payments_no_govuk_payment_found(self):
        """
        Test that if GOV.UK Pay returns 404 for one payment, the command marks the related
        MTP payment as failed.
        """
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % self.processor_id),
                status=404,
            )
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % self.ref),
                json={
                    **self.payment_data,
                    'status': 'failed',
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            self.assertEqual(rsps.calls[3].request.body.decode(), '{"status": "failed"}')

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def test_update_incomplete_payments_doesnt_sent_email_if_no_captured_date(self):
        """
        Test that if the MTP payment is in 'pending' and the GOV.UK payment is in 'success'
        but no captured_date is found in the response, the MTP payment is not marked
        as successful yet and no email is sent.

        This is because the actual capture in Worldpay happens at a later time and can fail.
        The only way to find out if a payment really went through is by checking the captured date,
        when it becomes available.
        """
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % self.processor_id),
                json={
                    'reference': self.ref,
                    'state': {'status': 'success'},
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )
            # no call to mark the payment as successful

            call_command('update_incomplete_payments', verbosity=0)

            self.assertEqual(len(mail.outbox), 0)

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def _test_update_incomplete_payments_doesnt_update_before_capture(self, settlement_summary):
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % self.processor_id),
                json={
                    'reference': self.ref,
                    'state': {'status': 'success'},
                    'settlement_summary': settlement_summary,
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            self.assertEqual(len(mail.outbox), 0)

    def test_update_incomplete_payments_doesnt_update_with_missing_captured_date(self):
        self._test_update_incomplete_payments_doesnt_update_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
        })

    def test_update_incomplete_payments_doesnt_update_with_null_capture_time(self):
        self._test_update_incomplete_payments_doesnt_update_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': None
        })

    def test_update_incomplete_payments_doesnt_update_with_blank_capture_time(self):
        self._test_update_incomplete_payments_doesnt_update_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': ''
        })

    def test_update_incomplete_payments_doesnt_update_with_invalid_capture_time(self):
        self._test_update_incomplete_payments_doesnt_update_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': '2015'
        })

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    @mock.patch(
        'send_money.payments.PaymentClient.get_security_check_result',
        mock.Mock(return_value=CheckResult.capture),
    )
    def test_captured_payment_with_captured_date_gets_updated(self):
        """
        Test that when a MTP pending payment is captured, if the captured date
        is immediately available, the payment is marked as 'taken' and a confirmation
        email is sent.
        """
        payment_id = 'payment-id'
        govuk_payment_data = {
            'payment_id': payment_id,
            'reference': self.ref,
            'state': {'status': 'capturable'},
            'email': 'success_sender@outside.local',
        }
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            # get govuk payment
            rsps.add(
                rsps.GET,
                govuk_url(f'/payments/{self.processor_id}/'),
                json=govuk_payment_data,
                status=200,
            )
            # capture payment
            rsps.add(
                rsps.POST,
                govuk_url(f'/payments/{payment_id}/capture/'),
                status=204,
            )
            # get govuk payment to see if we have the captured date
            rsps.add(
                rsps.GET,
                govuk_url(f'/payments/{self.processor_id}/'),
                json={
                    **govuk_payment_data,
                    'state': {'status': 'success'},
                    'settlement_summary': {
                        'capture_submit_time': '2016-10-27T15:11:05Z',
                        'captured_date': '2016-10-27',
                    },
                },
                status=200,
            )
            # update status
            rsps.add(
                rsps.PATCH,
                api_url(f'/payments/{self.ref}/'),
                json={
                    **self.payment_data,
                    'status': 'taken',
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            self.assertEqual(len(mail.outbox), 1)

            self.assertEqual(
                json.loads(rsps.calls[-1].request.body.decode()),
                {
                    'status': 'taken',
                    'received_at': '2016-10-27T15:11:05+00:00',
                },
            )

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def _test_captured_payment_doesnt_get_updated_before_capture(self, settlement_summary):
        payment_id = 'payment-id'
        govuk_payment_data = {
            'payment_id': payment_id,
            'reference': self.ref,
            'state': {'status': 'capturable'},
            'email': 'success_sender@outside.local',
        }
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url(f'/payments/{self.processor_id}/'),
                json=govuk_payment_data,
                status=200,
            )
            rsps.add(
                rsps.POST,
                govuk_url(f'/payments/{payment_id}/capture/'),
                status=204,
            )
            rsps.add(
                rsps.GET,
                govuk_url(f'/payments/{self.processor_id}/'),
                json={
                    **govuk_payment_data,
                    'state': {'status': 'success'},
                    'settlement_summary': settlement_summary,
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

        self.assertEqual(len(mail.outbox), 0)

    @mock.patch(
        'send_money.payments.PaymentClient.get_security_check_result',
        mock.Mock(return_value=CheckResult.capture),
    )
    def test_captured_payment_doesnt_get_updated_with_missing_captured_date(self):
        self._test_captured_payment_doesnt_get_updated_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
        })

    @mock.patch(
        'send_money.payments.PaymentClient.get_security_check_result',
        mock.Mock(return_value=CheckResult.capture),
    )
    def test_captured_payment_doesnt_get_updated_with_null_capture_time(self):
        self._test_captured_payment_doesnt_get_updated_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': None
        })

    @mock.patch(
        'send_money.payments.PaymentClient.get_security_check_result',
        mock.Mock(return_value=CheckResult.capture),
    )
    def test_captured_payment_doesnt_get_updated_with_blank_capture_time(self):
        self._test_captured_payment_doesnt_get_updated_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': ''
        })

    @mock.patch(
        'send_money.payments.PaymentClient.get_security_check_result',
        mock.Mock(return_value=CheckResult.capture),
    )
    def test_captured_payment_doesnt_get_updated_with_invalid_capture_time(self):
        self._test_captured_payment_doesnt_get_updated_before_capture({
            'capture_submit_time': '2016-10-27T15:11:05Z',
            'captured_date': '2015'
        })

    @override_settings(ENVIRONMENT='prod')  # because non-prod environments don't send to @outside.local
    def _test_received_at_date_matches_captured_date(self, capture_submit_time, captured_date, received_at):
        with responses.RequestsMock() as rsps:
            mock_auth(rsps)
            rsps.add(
                rsps.GET,
                api_url('/payments/'),
                json=self.payment_data,
                status=200,
            )
            rsps.add(
                rsps.GET,
                govuk_url('/payments/%s/' % self.processor_id),
                json={
                    'reference': self.ref,
                    'state': {'status': 'success'},
                    'settlement_summary': {
                        'capture_submit_time': capture_submit_time,
                        'captured_date': captured_date
                    },
                    'email': 'success_sender@outside.local',
                },
                status=200
            )
            rsps.add(
                rsps.PATCH,
                api_url('/payments/%s/' % self.ref),
                json={
                    **self.payment_data,
                    'email': 'success_sender@outside.local',
                },
                status=200,
            )

            call_command('update_incomplete_payments', verbosity=0)

            self.assertEqual(
                json.loads(rsps.calls[-1].request.body.decode())['received_at'],
                received_at
            )

    def test_submit_time_used_when_date_the_same(self):
        self._test_received_at_date_matches_captured_date(
            '2016-10-28T14:57:05Z',
            '2016-10-28',
            '2016-10-28T14:57:05+00:00'
        )

    def test_received_at_date_is_put_forward(self):
        self._test_received_at_date_matches_captured_date(
            '2016-10-27T23:57:05Z',
            '2016-10-28',
            '2016-10-28T00:00:00+00:00'
        )

    # Assume captured_date is UTC. May not be a correct assumption, but it's
    # the only one that can work.
    def test_received_at_date_takes_timezones_into_account(self):
        self._test_received_at_date_matches_captured_date(
            '2016-10-28T00:57:05+01:00',
            '2016-10-28',
            '2016-10-28T00:00:00+00:00'
        )

    @mock.patch('mtp_send_money.apps.send_money.payments.timezone.now')
    def test_received_at_date_is_set_to_now_when_submit_time_absent(self, mock_now):
        mock_now.return_value = datetime(2016, 10, 28, 12, 45, 22, tzinfo=utc)
        self._test_received_at_date_matches_captured_date(
            '',
            '2016-10-28',
            '2016-10-28T12:45:22+00:00'
        )

    @mock.patch('mtp_send_money.apps.send_money.payments.timezone.now')
    def test_received_at_date_is_put_back(self, mock_now):
        mock_now.return_value = datetime(2016, 10, 29, 0, 5, 22, tzinfo=utc)
        self._test_received_at_date_matches_captured_date(
            '',
            '2016-10-28',
            '2016-10-28T23:59:59.999999+00:00'
        )
