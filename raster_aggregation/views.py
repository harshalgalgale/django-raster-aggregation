import mapbox_vector_tile
from raster.tiles.const import WEB_MERCATOR_SRID
from raster.tiles.utils import tile_bounds
from rest_framework import filters, viewsets
from rest_framework.exceptions import APIException
from rest_framework_extensions.cache.decorators import cache_response
from rest_framework_gis.filters import InBBOXFilter

from django.contrib.gis.db.models.functions import Intersection, Transform
from django.contrib.gis.gdal import OGRGeometry
from django.http import Http404, HttpResponse
from django.views.generic import View

from .models import AggregationArea, AggregationLayer, AggregationLayerGroup
from .serializers import (
    AggregationAreaGeoSerializer, AggregationAreaSimplifiedSerializer, AggregationAreaValueSerializer,
    AggregationLayerSerializer
)


class MissingQueryParameter(APIException):
    status_code = 500
    default_detail = 'Missing Query Parameter.'


class AggregationAreaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Regular aggregation Area model view endpoint.
    """
    serializer_class = AggregationAreaSimplifiedSerializer
    filter_fields = ('aggregationlayer', )

    def get_queryset(self):
        qs = AggregationArea.objects.all()
        ids = self.request.query_params.get('ids')
        if ids:
            qs = qs.filter(id__in=ids.split(','))
        return qs

    @cache_response(key_func='calculate_cache_key')
    def list(self, request, *args, **kwargs):
        """
        List method wrapped with caching decorator.
        """
        return super(AggregationAreaViewSet, self).list(request, *args, **kwargs)

    def calculate_cache_key(self, view_instance, view_method, request, *args, **kwargs):
        """
        Creates the cache key based on query parameters and change dates from
        related objects.
        """
        # Add ids to cache key data
        cache_key_data = [
            request.GET.get('ids', '')
        ]

        # Add aggregationlayer id and modification date
        agglayer_id = request.GET.get('aggregationlayer', '')
        if agglayer_id:
            modified = AggregationLayer.objects.get(id=agglayer_id).modified
            modified = str(modified).replace(' ', '-')
            cache_key_data.append('-'.join(['agg', agglayer_id, modified]))

        return '|'.join(cache_key_data)


class AggregationAreaValueViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Regular aggregation Area model view endpoint.
    """
    serializer_class = AggregationAreaValueSerializer
    filter_fields = ('aggregationlayer', )

    def initial(self, request, *args, **kwargs):
        """
        Look for required request query parameters.
        """
        if 'formula' not in request.GET:
            raise MissingQueryParameter(detail='Missing query parameter: formula')
        elif 'layers' not in request.GET:
            raise MissingQueryParameter(detail='Missing query parameter: layers')

        return super(AggregationAreaValueViewSet, self).initial(request, *args, **kwargs)

    def get_queryset(self):
        qs = AggregationArea.objects.all()
        ids = self.request.query_params.get('ids')
        if ids:
            ids = ids.split(',')
            return qs.filter(id__in=ids)
        return qs


class AggregationAreaGeoViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that returns Aggregation Area geometries in GeoJSON format.
    """
    serializer_class = AggregationAreaGeoSerializer
    allowed_methods = ('GET', )
    filter_backends = (InBBOXFilter, filters.DjangoFilterBackend, )
    filter_fields = ('name', 'aggregationlayer', )
    bbox_filter_field = 'geom'
    paginate_by = None
    bbox_filter_include_overlapping = True

    def get_queryset(self):
        queryset = AggregationArea.objects.all()
        zoom = self.request.QUERY_PARAMS.get('zoom', None)
        if zoom:
            queryset = queryset.filter(aggregationlayer__min_zoom_level__lte=zoom, aggregationlayer__max_zoom_level__gte=zoom)
        return queryset


class AggregationLayerViewSet(viewsets.ReadOnlyModelViewSet):

    serializer_class = AggregationLayerSerializer

    def get_queryset(self):
        return AggregationLayer.objects.all()


class VectorTilesView(View):

    def get(self, request, layergroup, x, y, z, response_format, *args, **kwargs):
        # Select which agglayer to use for this tile.
        grp = AggregationLayerGroup.objects.get(id=layergroup)
        layerzoomrange = grp.aggregationlayerzoomrange_set.filter(
            min_zoom__lte=z,
            max_zoom__gte=z,
        ).first()
        if not layerzoomrange:
            raise Http404('No layer found for this zoom level')
        lyr = layerzoomrange.aggregationlayer

        # Compute intersection between the tile boundary and the layer geometries.
        bounds = tile_bounds(int(x), int(y), int(z))
        bounds = OGRGeometry.from_bbox(bounds)
        bounds.srid = WEB_MERCATOR_SRID
        bounds = bounds.geos
        result = AggregationArea.objects.filter(
            aggregationlayer=lyr,
            geom__intersects=bounds,
        ).annotate(
            intersection=Transform(Intersection('geom', bounds), WEB_MERCATOR_SRID)
        ).only('id', 'name')

        # Render intersection as vector tile in two different available formats.
        if response_format == '.json':
            result = ['{{"geometry": {0}, "properties": {{"id": {1}, "name": "{2}"}}}}'.format(dat.intersection.geojson, dat.id, dat.name) for dat in result]
            result = ','.join(result)
            result = '{"type": "FeatureCollection","features":[' + result + ']}'
            return HttpResponse(result, content_type="application/json")
        elif response_format == '.pbf':
            result = [{"geometry": bytes(dat.intersection.wkb), "properties": {"id": dat.id, "name": dat.name}} for dat in result]
            result = mapbox_vector_tile.encode({"name": "testlayer", "features": result})
            return HttpResponse(result, content_type="application/octet-stream")
        else:
            raise Http404('Unknown response format {0}'.format(response_format))
