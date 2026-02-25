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
from builtins import object, range, map
from io import open

import time
import logging
import sys
import copy

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import numpy as np

try:
    import epics
except Exception:
    pass

class MonoAutoTune(object):
    def __init__(self, settings):
        self.settings = settings

        self._callbacks = []

        self._init_pvs()

        self._initialize()

    def _init_pvs(self):
        # Happens before create layout
        self.ao_pv, connected = self._initialize_pv('{}.VAL'.format(
            self.settings['device_data']['kwargs']['output']))
        self.ao_low_lim_pv, connected = self._initialize_pv('{}.LOPR'.format(
            self.settings['device_data']['kwargs']['output']))
        self.ao_high_lim_pv, connected = self._initialize_pv('{}.HOPR'.format(
            self.settings['device_data']['kwargs']['output']))

        self.ct_time_pv, connected = self._initialize_pv('{}'.format(
            self.settings['device_data']['kwargs']['ct_time']))
        self.ct_start_pv, connected = self._initialize_pv('{}'.format(
            self.settings['device_data']['kwargs']['ct_start']))
        self.ct_val_pv, connected = self._initialize_pv('{}'.format(
            self.settings['device_data']['kwargs']['ct_val']))

        self.i0_shutter_pv = self._initialize_pv('{}'.format(
            self.settings['exp_slow_shtr1']))

    def _initialize_pv(self, pv_name):
        pv = epics.get_pv(pv_name)
        connected = pv.wait_for_connection(5)

        if not connected:
            logger.error('Failed to connect to EPICS PV %s on startup', pv_name)

        return pv, connected

    def _initialize(self):
        self.step_start = self.settings['optimize_step']
        self.step_min = self.settings['optimize_min_step']
        self.step_scale = self.settings['optimize_step_scale']

        self.low_lim = self.ao_low_lim_pv.get()
        self.high_lim = self.ao_high_lim_pv.get()

    def _measure_intensity(self):
        self.ct_start_pv.put()

        while not self.ct_start_pv.get() == 1:
            time.sleep(0.01)

        while self.ct_start_pv.get() == 1:
            time.sleep(0.01)

        val = self.ct_val_pv.get(use_monitor=False)

        return val

    def optimize_intensity(self):
        self.ct_time_pv.put(self.settings['optimize_ct_time'], wait=True)
        self.i0_shutter_pv.put(0, wait=True)
        time.sleep(0.1) #Waits for shutter to open

        v_start = self.ao_pv.get()
        i_start = self._measure_intensity()

        logger.info('Starting optimizing I0 intensity. Initial: %s cts/s at %s V',
            i_start/self.settings['optimize_ct_time'], v_start)

        i_best, v_best, improved = self._search_up(self.step_start, i_start, v_start)

        if not improved:
            i_best, v_best, improved = self._search_down(self.step_start, i_start, v_start)
            search_dir = 'up'
        else:
            search_dir = 'down'

        step = self.step_start

        while step > self.step_min:
            step = max(self.step_min, step/self.step_scale)

            if search_dir == 'up':
                i_best, v_best, improved = self._search_up(step, i_best, v_best)
                search_dir = 'down'
            else:
                i_best, v_best, improved = self._search_down(step, i_best, v_best)
                search_dir = 'up'

        self.i0_shutter_pv.put(1)

        logger.info('Finished optimizing I0 intensity. Final: %s cts/s at %s V',
            i_best/self.settings['optimize_ct_time'], v_best)

    def _search_up(self, step, i_start, v_start):
        i_new = np.inf
        i_prev = copy.copy(i_start)
        v_new = v_start

        while i_new > i_prev and v_new != self.high_lim:
            v_new += step

            if v_new > self.high_lim:
                v_new = self.high_lim

            self.ao_pv.put(v_new, wait=True)
            # Need a settle/wait here?

            if not np.isinf(i_new):
                i_prev = copy.copy(i_new)

            i_new = self._measure_intensity()

        if v_new == v_start + step:
            improved = False
            i_best = i_start
            v_best = v_start
        else:
            improved = True
            i_best = i_new
            v_best = v_new

        return i_best, v_best, improved

    def _search_down(self, step, i_start, v_start):
        i_new = np.inf
        i_prev = copy.copy(i_start)
        v_new = v_start

        while i_new > i_prev and v_new != self.low_lim:
            v_new -= step

            if v_new < self.low_lim:
                v_new = self.low_lim

            self.ao_pv.put(v_new, wait=True)
            # Need a settle/wait here?

            if not np.isinf(i_new):
                i_prev = copy.copy(i_new)

            i_new = self._measure_intensity()

        if v_new == v_start - step:
            improved = False
            i_best = i_start
            v_best = v_start
        else:
            improved = True
            i_best = i_new
            v_best = v_new

        return v_best, i_best, improved


#Settings
default_mono_tune_settings = {
    'device_init'           : [
        {'name': 'Mono Tune', 'args': [], 'kwargs': {
            'output'        : '18ID:USB1608G_2AO_1:Ao1',
            'ct_time'       : '18ID:scaler2.TP',
            'ct_start'      : '18ID:scaler2.CNT',
            'ct_val'        : '18ID:scaler2.S3',
            }
        },
        ], # Compatibility with the standard format
    'optimize_step'         : 0.1, #Initial optimize step value in V
    'optimize_min_step'     : 0.01, #Minimum optimize step size in V
    'optimize_step_scale'   : 2, #Scaling factor for reducing step size in search
    'optimize_ct_time'      : 0.05, #Joerger count time or optimize
    'fe_shutter'            : 'PA:18ID:STA_A_FES_OPEN_PL',
    'd_shutter'             : 'PA:18ID:STA_D_SDS_OPEN_PL',
    'fe_shutter_open'       : '18ID:rshtr:A:OPEN',
    'fe_shutter_close'      : '18ID:rshtr:A:CLOSE',
    'd_shutter_open'        : '18ID:rshtr:D:OPEN',
    'd_shutter_close'       : '18ID:rshtr:D:CLOSE',
    'exp_slow_shtr1'        : '18ID:LJT4:3:Bi6',
    'device_communication'  : 'local',
    'remote_device'         : 'tuning', #Ignore
    'remote_ip'             : '164.54.204.53', #Ignore
    'remote_port'           : '5557', #Ignore
    'remote'                : False,
    'com_thread'            : None,
    'components'            : [],
    }


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    # h1.setLevel(logging.ERROR)

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

    settings = default_mono_tune_settings
    settings['components'] = ['mono_auto_tune']


    auto_tune = MonoAutoTune(settings)
    auto_tune.optimize_intensity()

