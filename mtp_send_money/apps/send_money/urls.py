from django.conf import settings
from django.conf.urls import url

from send_money import views

app_name = 'send_money'

if settings.SHOW_DEBIT_CARD_OPTION and settings.SHOW_BANK_TRANSFER_OPTION:
    urlpatterns = [
        url(r'^$', views.ChooseMethodView.as_view(template_name='send_money/choose-method.html'),
            name='choose_method'),
        url(r'^start-payment/$', views.SendMoneyView.as_view(),
            name='send_money_debit'),
        url(r'^prisoner-details/$', views.SendMoneyBankTransferView.as_view(),
            name='send_money_bank'),
    ]
elif settings.SHOW_DEBIT_CARD_OPTION:
    urlpatterns = [
        url(r'^$', views.SendMoneyView.as_view(), name='send_money_debit'),
    ]
elif settings.SHOW_BANK_TRANSFER_OPTION:
    urlpatterns = [
        url(r'^$', views.SendMoneyBankTransferView.as_view(), name='send_money_bank'),
    ]
else:
    urlpatterns = []

if settings.SHOW_BANK_TRANSFER_OPTION:
    urlpatterns += [
        url(r'^bank-transfer/$', views.bank_transfer_view, name='bank_transfer'),
    ]

if settings.SHOW_DEBIT_CARD_OPTION:
    urlpatterns += [
        url(r'^check-details/$', views.CheckDetailsView.as_view(), name='check_details'),
        url(r'^clear-session/$', views.clear_session_view, name='clear_session'),
        url(r'^card-payment/$', views.debit_card_view, name='debit_card'),
        url(r'^confirmation/$', views.confirmation_view, name='confirmation'),
    ]
