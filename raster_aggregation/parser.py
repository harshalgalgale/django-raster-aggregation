import os
import shutil
import tempfile
import zipfile

from celery.contrib.methods import task

from django.contrib.gis.gdal import CoordTransform, DataSource, SpatialReference
from raster_aggregation.utils import WEB_MERCATOR_SRID, convert_to_multipolygon


class AggregationDataParser(object):

    @task()
    def parse(self):
        """
        This method pushes the shapefile data from the AggregationLayer
        into the AggregationArea table.
        """
        # Clean previous parse log
        self.log('Started parsing Aggregation Layer {0}'.format(self.id))

        tmpdir = tempfile.mkdtemp()

        shapefilename = os.path.basename(self.shapefile.name)

        shapefilepath = os.path.join(tmpdir, shapefilename)

        # Get zipped shapefile from storage
        try:
            # Access shapefile and store locally
            shapefile = open(shapefilepath, 'wb')
            for chunk in self.shapefile.chunks():
                shapefile.write(chunk)
            shapefile.close()
        except:
            shutil.rmtree(tmpdir)
            self.log('Error: Could not download file, aborted parsing')
            return

        # Open and extract zipfile
        try:
            zf = zipfile.ZipFile(shapefilepath)
            zf.extractall(tmpdir)
        except:
            shutil.rmtree(tmpdir)
            self.log('Error: Could not open zipfile, aborted parsing')
            return

        # Remove zipfile
        os.remove(shapefilepath)

        # Set shapefile as datasource for GDAL and get layer
        try:
            ds = DataSource(tmpdir)
            lyr = ds[0]
        except:
            shutil.rmtree(tmpdir)
            self.log('Error: Failed to extract layer from shapefile, aborted parsing')
            return

        # Check if name column exists
        if self.name_column.lower() not in [field.lower() for field in lyr.fields]:
            self.log(
                'Error: Name column "{0}" not found, aborted parsing. '
                'Available columns: {1}'.format(self.name_column, lyr.fields)
            )
            return

        # Setup transformation to default ref system
        try:
            ct = CoordTransform(lyr.srs, SpatialReference(WEB_MERCATOR_SRID))
        except:
            shutil.rmtree(tmpdir)
            self.log('Error: Layer srs not specified, aborted parsing')
            return

        # Remove existing patches before re-creating them
        self.aggregationarea_set.all().delete()

        # Loop through features
        for feat in lyr:
            # Get geometry and transform to WGS84
            try:
                wgsgeom = feat.geom
                wgsgeom.transform(ct)
            except:
                self.log('Warning: Failed to transform feature fid {0}\n'.format(feat.fid))
                continue

            try:
                # Ignore z-dim
                wgsgeom.coord_dim = 2

                # Assure that feature is a valid multipolygon
                geom = convert_to_multipolygon(wgsgeom.geos)
            except:
                self.log(
                    'Warning: Failed to convert feature fid {0} to'
                    ' multipolygon\n'.format(feat.fid)
                )
                continue

            # Add warning if geom is not valid
            if geom.valid_reason != 'Valid Geometry':
                self.log(
                    'Warning: Found invalid geometry for'
                    ' feature fid {0}\n'.format(feat.fid)
                )
                continue

            # If geom is empty, conversion was not successful, issue
            # warning and continue
            if geom.empty:
                self.log(
                    'Warning: Failed to convert feature fid'
                    ' {0} to valid geometry\n'.format(feat.fid)
                )
                continue

            # Create aggregation area
            try:
                self.aggregationarea_set.create(
                    name=feat.get(self.name_column),
                    aggregationlayer=self,
                    geom=geom
                )
            except:
                self.log(
                    'Warning: Failed to create AggregationArea '
                    'for feature fid {0}\n'.format(feat.fid)
                )

        self.log('Finished parsing Aggregation Layer {0}'.format(self.id))

        # Remove tempdir with unzipped shapefile
        shutil.rmtree(tmpdir)
