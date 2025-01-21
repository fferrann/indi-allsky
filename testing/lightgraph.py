#!/usr/bin/env python3

import math
from datetime import datetime
from datetime import timedelta
import time
import ephem
import numpy
import cv2
import logging


LATITUDE  = 73
LONGITUDE = -84



logging.basicConfig(level=logging.INFO)
logger = logging


class LightGraphGenerator(object):

    graph_height = 50
    now_size = 8
    light_color = (200, 200, 200)
    dark_color = (15, 15, 15)
    line_color = (15, 15, 200)
    border_color = (200, 200, 15)
    now_color = (15, 200, 15)


    def __init__(self):
        self.obs = ephem.Observer()
        self.obs.lon = math.radians(LONGITUDE)
        self.obs.lat = math.radians(LATITUDE)

        # disable atmospheric refraction calcs
        self.obs.pressure = 0

        self.sun = ephem.Sun()
        self.moon = ephem.Moon()


        self.utc_offset = None
        self.next_generate = None


    def main(self):
        #utcnow_notz = now - utc_offset
        self.generate()


    def generate(self):
        generate_start = time.time()


        now = datetime.now()
        self.utc_offset = now.astimezone().utcoffset()

        noon = datetime.strptime(now.strftime('%Y%m%d12'), '%Y%m%d%H')
        self.next_generate = noon + timedelta(hours=24)

        noon_utc = noon - self.utc_offset


        now_offset = int((now - noon).seconds / 60)
        #logger.info('Now offset: %d', now_offset)


        lightgraph_list = list()
        for x in range(1440):
            self.obs.date = noon_utc + timedelta(minutes=x)
            self.sun.compute(self.obs)

            sun_alt_deg = math.degrees(self.sun.alt)

            if sun_alt_deg < -18:
                lightgraph_list.append(self.dark_color)
            elif sun_alt_deg > 0:
                lightgraph_list.append(self.light_color)
            else:
                norm = (18 + sun_alt_deg) / 18  # alt is negative
                lightgraph_list.append(self.mapColor(norm, self.light_color, self.dark_color))

        #logger.info(lightgraph_list)

        generate_elapsed_s = time.time() - generate_start
        logger.warning('Total lightgraph processing in %0.4f s', generate_elapsed_s)


        lightgraph = numpy.array([lightgraph_list], dtype=numpy.uint8)
        lightgraph = cv2.resize(
            lightgraph,
            (1440, self.graph_height),
            interpolation=cv2.INTER_AREA,
        )


        # draw hour ticks
        for x in range(1, 24):
            cv2.line(
                img=lightgraph,
                pt1=(60 * x, 0),
                pt2=(60 * x, self.graph_height),
                color=tuple(self.line_color),
                thickness=1,
                lineType=cv2.LINE_AA,
            )


        # draw now triangle
        now_tri = numpy.array([
            [now_offset - self.now_size, self.graph_height - self.now_size],
            [now_offset + self.now_size, self.graph_height - self.now_size],
            [now_offset, self.graph_height]
        ],
            dtype=numpy.int32,
        )

        cv2.fillPoly(
            img=lightgraph,
            pts=[now_tri],
            color=self.now_color,
        )


        # draw border
        lightgraph = cv2.copyMakeBorder(
            lightgraph,
            5,
            5,
            5,
            5,
            cv2.BORDER_CONSTANT,
            None,
            self.border_color,
        )


        logger.info(lightgraph.shape)

        cv2.imwrite('lightgraph.jpg', lightgraph, [cv2.IMWRITE_JPEG_QUALITY, 90])


    def mapColor(self, scale, color_high, color_low):
        return tuple(int(((x[0] - x[1]) * scale) + x[1]) for x in zip(color_high, color_low))


if __name__ == "__main__":
    LightGraphGenerator().main()
