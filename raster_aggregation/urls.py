from rest_framework import routers

from django.conf.urls import include, url

from .views import AggregationAreaValueViewSet, VectorTilesView

router = routers.DefaultRouter()

router.register(r'aggregationareavalue', AggregationAreaValueViewSet, base_name='aggregationareavalue')

urlpatterns = [

    url(r'api/', include(router.urls)),
    # Vector tiles endpoint.
    url(
        r'^vtiles/(?P<layergroup>[^/]+)/(?P<z>[0-9]+)/(?P<x>[0-9]+)/(?P<y>[0-9]+)(?P<response_format>\.json|\.pbf)$',
        VectorTilesView.as_view(),
        name='vector_tiles'
    ),

]
