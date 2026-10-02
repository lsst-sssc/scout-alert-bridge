from django.urls import include, path

# The whole TOM Toolkit URLconf, not just the admin, even though nothing serves HTTP in
# production. tomtoolkit 3.1 wraps the admin login in allauth's secure_admin_login and its
# middleware reverses 'login', 'home' and the account_* names at startup, so an admin-only
# URLconf raises NoReverseMatch; and tom_dataservices.to_target() reverses
# 'targets:detail' / 'targets:create' when it catches a duplicate-target IntegrityError,
# without which rundataquery aborts the batch on the first already-seen candidate.
urlpatterns = [
    path('', include('tom_common.urls')),
]
