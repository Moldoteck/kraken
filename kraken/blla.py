# -*- coding: utf-8 -*-
#
# Copyright 2019 Benjamin Kiessling
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing
# permissions and limitations under the License.
"""
kraken.blla
~~~~~~~~~~~~~~

Trainable baseline layout analysis tools for kraken
"""

import torch
import logging
import numpy as np
import pkg_resources
import torch.nn.functional as F
import torchvision.transforms as tf

from kraken.lib import vgsl, dataset
from kraken.lib.util import pil2array, is_bitonal, get_im_str
from kraken.lib.exceptions import KrakenInputException
from kraken.lib.segmentation import polygonal_reading_order, vectorize_lines, scale_polygonal_lines, calculate_polygonal_environment

__all__ = ['segment']

logger = logging.getLogger(__name__)

def segment(im,
            text_direction='horizontal-lr',
            mask=None,
            reading_order_fn=polygonal_reading_order,
            model=None,
            device='cpu'):
    """
    Segments a page into text lines using the baseline segmenter.

    Segments a page into text lines and returns the polyline formed by each
    baseline and their estimated environment.

    Args:
        im (PIL.Image): An RGB image.
        text_direction (str): Ignored by the segmenter but kept for
                              serialization.
        mask (PIL.Image): A bi-level mask image of the same size as `im` where
                          0-valued regions are ignored for segmentation
                          purposes. Disables column detection.
        reading_order_fn (function): Function to determine the reading order.
                                     Has to accept a list of tuples (baselines,
                                     polygon) and a text direction (`lr` or
                                     `rl`).
        model (vgsl.TorchVGSLModel): A TorchVGSLModel containing a segmentation
                                     model. If none is given a default model
                                     will be loaded.
        device (str or torch.Device): The target device to run the neural
                                      network on.

    Returns:
        {'text_direction': '$dir',
         'type': 'baseline',
         'lines': [
            {'baseline': [[x0, y0], [x1, y1], ..., [x_n, y_n]], 'boundary': [[x0, y0, x1, y1], ... [x_m, y_m]]},
            {'baseline': [[x0, ...]], 'boundary': [[x0, ...]]}
          ]
        }: A dictionary containing the text direction and under the key 'lines'
        a list of reading order sorted baselines (polylines) and their
        respective polygonal boundaries. The last and first point of each
        boundary polygon is connected.

    Raises:
        KrakenInputException if the input image is not binarized or the text
        direction is invalid.
    """
    im_str = get_im_str(im)
    logger.info('Segmenting {}'.format(im_str))

    if model is None:
        logger.info('No segmentation model given. Loading default model.')
        model = vgsl.TorchVGSLModel.load_model(pkg_resources.resource_filename(__name__, 'blla.mlmodel'))
    model.eval()
    model.to(device)

    if mask:
        if mask.mode != '1' and not is_bitonal(mask):
            logger.error('Mask is not bitonal')
            raise KrakenInputException('Mask is not bitonal')
        mask = mask.convert('1')
        if mask.size != im.size:
            logger.error('Mask size {} doesn\'t match image size {}'.format(mask.size, im.size))
            raise KrakenInputException('Mask size {} doesn\'t match image size {}'.format(mask.size, im.size))
        logger.info('Masking enabled in segmenter.')
        mask = pil2array(mask)

    batch, channels, height, width = model.input
    transforms = dataset.generate_input_transforms(batch, height, width, channels, 0, valid_norm=False)
    res_tf = tf.Compose(transforms.transforms[:3])
    scal_im = res_tf(im).convert('L')

    with torch.no_grad():
        logger.debug('Running network forward pass')
        o = model.nn(transforms(im).unsqueeze(0).to(device))
    logger.debug('Upsampling network output')
    o = F.interpolate(o, size=scal_im.size[::-1])
    o = o.squeeze().cpu().numpy()
    logger.debug('Vectorizing network output')
    baselines = vectorize_lines(o)
    logger.debug('Polygonizing lines')
    lines = list(filter(lambda x: x[1] is not None, zip(baselines, calculate_polygonal_environment(scal_im, baselines))))
    logger.debug('Scaling vectorized lines')
    scale = np.divide(im.size, o.shape[:0:-1])
    lines = scale_polygonal_lines(lines, scale)
    logger.debug('Reordering baselines')
    lines = reading_order_fn(lines, text_direction[-2:])
    return {'text_direction': text_direction,
            'type': 'baselines',
            'lines': [{'script': 'default', 'baseline': bl, 'boundary': pl} for bl, pl in lines]}
