from django.urls import path

from . import approval_task


urlpatterns = [
    path("admin/companies/pending/", approval_task.GetAllPendingApprovalsView.as_view(), name="v1-admin-companies-pending"),
    path("admin/sales-directors/", approval_task.GetAllSaleDirectorsView.as_view(), name="v1-admin-sales-directors"),
    path("admin/companies/status/", approval_task.ApprovedORRejectCompanyView.as_view(), name="v1-admin-company-status"),
    path("admin/bookings/paid/", approval_task.FetchPaidBookingView.as_view(), name="v1-admin-paid-bookings"),
    path("admin/bookings/payments/approve/", approval_task.ApproveBookingPaymentView.as_view(), name="v1-admin-booking-payment-approve"),
    path("admin/receivables/", approval_task.GetPartnerReceiveAblePaymentsView.as_view(), name="v1-admin-receivables"),
    path("admin/receivables/transfer/", approval_task.ManagePartnerReceiveAblePaymentView.as_view(), name="v1-admin-receivables-transfer"),
    path("admin/catalog/hotels/", approval_task.ManageMasterHotelsCatalogView.as_view(), name="v1-admin-master-hotels"),
]
