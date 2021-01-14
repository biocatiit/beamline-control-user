# coding: utf-8
#
#    Project: BioCAT user beamline control software (BioCON)
#             https://github.com/biocatiit/beamline-control-user
#
#
#    Principal author:       Jesse Hopkins
#
#    This is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This software is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this software.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import object, range, map
from io import open

import threading
import time
from collections import deque, OrderedDict
import logging
import sys
import copy
import platform
import os

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

pipeline_path = os.path.abspath(os.path.expanduser('~/Documents/software_dev/saxs-pipeline'))
if pipeline_path not in os.sys.path:
    os.sys.path.append(pipeline_path)

import pipeline

class PipelineControl(object):

    def __init__(self, settings):
        self.settings = settings
        self.port = self.settings['server_port']
        self.ip = self.settings['ip']

        self.cmd_q = deque()
        self.return_q = deque()
        self.abort_event = deque()
        self.timeout_event = deque()

        control_client = pipeline.client.ControlClient(self.ip, self.port,
            self.cmd_q, self.return_q, self.abort_event, self.timeout_event,
            name='PipelineCtrlClient')
        control_client.start()

        self.set_raw_settings(self.settings['raw_settings'])

        self.current_expeirment = ''

    def start_experiment(self, exp_name, exp_type, data_dir, fprefix, n_exps,
        n_sample_exps=0, sample_prefix=''):
        """
        Start experiment
        exp_name - should be unique experiment name (how to guarantee this? Just use fprefix?)
        exp_type -  'SEC' or 'Batch'
        data_dir - data directory for images
        fprefix - Image file prefix
        n_exps - Number of exposures in the experiment

        In the event of a batch mode experiment, expect fprefix to be the buffer
        prefix, n_exps to be the buffer number of exposures
        """
        self.current_expeirment = exp_name

        if n_sample_exps == 0:
            n_sample_exps = n_exps

        if sample_prefix == '':
            sample_prefix = fprefix

        if exp_type == 'SEC':
            cmd_kwargs = {'num_exps': n_exps}
        elif exp_type == 'Batch':
            cmd_kwargs = {'num_sample_exps': n_sample_exps,
            'num_buffer_exps': n_exps, 'sample_prefix': sample_prefix,
            'buffer_prefix': fprefix}

        cmd = ('start_experiment', [exp_name, exp_type, data_dir, fprefix],
            cmd_kwargs)
        client_cmd = {'command': cmd, 'response': False}
        self.cmd_q.append(client_cmd)

    def stop_experiment(self, exp_name):
        """
        Stop experiment
        exp_name - The experiment name to stop data collection for in the pipeline
        """
        cmd = ('stop_experiment', [exp_name,])
        client_cmd = {'command': cmd, 'response': False}
        self.cmd_q.append(client_cmd)

    def stop_current_experiment(self):
        if self.current_expeirment != '':
            cmd = ('stop_experiment', [self.current_expeirment,])
            client_cmd = {'command': cmd, 'response': False}
            self.cmd_q.append(client_cmd)

    def set_raw_settings(self, settings_file):
        """
        Pipeline loads a new RAW settings file
        settings_file - The settings file to load (must be accessible to pipeline,
            and path must be on pipeline computer)
        """
        cmd = ('load_raw_settings', [settings_file,])
        client_cmd = {'command': cmd, 'response': False}
        self.cmd_q.append(client_cmd)

    def stop(self):
        """
        Stops client cleanly
        """
        self.control_client.stop()
        self.control_client.join()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.ERROR)

    # formatter = logging.Formatter('%(asctime)s - %(message)s')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # logger = logging.getLogger('biocon')
    # logger.setLevel(logging.DEBUG)
    # h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h1.setFormatter(formatter)
    # logger.addHandler(h1)

    #Settings
    settings = {
        'components'    : ['pipeline'],
        'server_port'   : '5556',
        'server_ip'     : '192.168.1.14',
        'raw_settings'  : '../data/UO_SEC/SAXS.cfg',
        }


