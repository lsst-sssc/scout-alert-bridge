from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    # Not a served web UI (this project is headless) — but tom_dataservices.to_target()
    # calls reverse('targets:detail', ...) / reverse('targets:create') when it catches a
    # duplicate-target IntegrityError, so the 'targets' namespace must resolve or
    # rundataquery aborts the whole batch on the first already-seen candidate.
    path('targets/', include('tom_targets.urls', namespace='targets')),
]
