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

import traceback
import threading
import time
import collections
from collections import OrderedDict, deque
import queue
import logging
import sys
import copy
import platform
import datetime

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import numpy as np

# Uses stellarnet python driver, available from the manufacturer
import stellarnet_driver3 as sn

class Spectrometer(object):

    def __init__(self, name, history_time=60*60*24):
        """
        Spectrometer. Note that spectrum are expected to be returned as
        numpy arrays n x 2 arrays where each n datapoint is [lambda, spectral value].

        Parameters
        ----------
        name: str
            The name of the device.
        history_time: float, optional
            The length of time to retain spectrum in the local history
        """
        self.name = name

        self._history_length = history_time

        self._history = {'spectra' : [], 'timestamps' : []}
        self._transmission_history = {'spectra' : [], 'timestamps' : []}
        self._absorbance_history = {'spectra' : [], 'timestamps' : []}

        self._taking_data = False
        self._taking_series = False
        self._reference_spectrum = None
        self._dark_spectrum = None
        self._series_abort_event = threading.Event()
        self._series_thread = None

        self._integration_time = 1
        self._scan_avg = 1
        self._smoothing = 0


    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.name)

    def __str__(self):
        return '{} {}'.format(self.__class__.__name__, self.name)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def set_integration_time(self, int_time, update_dark=True):
        pass

    def set_scan_avg(self, num_avgs, update_dark=True):
        pass

    def set_smoothing(self, smooth, update_dark=True):
        pass

    def lightsource_shutter(self, open):
        pass

    def _collect_spectrum(self, int_trigger):
        pass

    def _check_dark_conditions(self, set_dark_conditions=True):
        """
        Checks whether the spectrometer is dark

        Parameters
        ----------
        set_dark_conditions: bool, optional
            If True (default) will attempt to set dark conditions properly

        Returns
        -------
        is_dark: bool
            Whether spectrometer is currently in a dark condition
        """
        pass

    def is_busy(self):
        return self._taking_data or self._taking_series

    def get_integration_time(self):
        return self._integration_time

    def get_scan_avg(self):
        return self._scan_avg

    def get_smoothing(self):
        return self._smoothing

    def set_dark(self, spectrum, timestamp):
        self._dark_spectrum = [timestamp, spectrum]

    def get_dark(self):
        if self._dark_spectrum is None:
            raise RuntimeError('No dark spectrum')

        return self._dark_spectrum

    def collect_dark(self, averages=1, set_dark_conditions=True):
        if not self.is_busy():
            is_dark = self._check_dark_conditions(
                set_dark_conditions=set_dark_conditions)

            if is_dark:
                all_spectra = []

                for i in range(averages):
                    spectrum = self._collect_spectrum(True)
                    timestamp = datetime.datetime.now()

                    all_spectra.append(spectrum)

                    if i == 0:
                        initial_timestamp = timestamp

                if averages > 1:
                    avg_timestamp = initial_timestamp + (timestamp-initial_timestamp)/2
                    avg_spectrum = np.mean(all_spectra, axis=0)
                else:
                    avg_timestamp = initial_timestamp
                    avg_spectrum = all_spectra[0]

                self.set_dark(avg_spectrum, avg_timestamp)
            else:
                raise RuntimeError('Spectrometer is not in dark conditions, so '
                    'a dark reference spectrum could not be collected.')

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

    def set_reference_spectrum(self, spectrum, timestamp):
        self._reference_spectrum = [timestamp, spectrum]

    def get_reference_spectrum(self):
        if self._reference_spectrum is None:
            raise RuntimeError('No reference spectrum')

        return self._reference_spectrum

    def collect_reference_spectrum(self, averages=1, dark_correct=True, int_trigger=True):
        all_spectra = []

        for i in range(averages):
            timestamp, spectrum = self.get_spectrum(False, dark_correct, int_trigger)
            all_spectra.append(spectrum)

            if i == 0:
                initial_timestamp = timestamp

        if averages > 1:
            avg_timestamp = initial_timestamp + (timestamp-initial_timestamp)/2
            avg_spectrum = np.mean(all_spectra, axis=0)
        else:
            avg_timestamp = initial_timestamp
            avg_spectrum = all_spectra[0]

        self.set_reference_spectrum(avg_spectrum, avg_timestamp)

    def get_spectrum(self, spec_type='abs', dark_correct=True, int_trigger=True):
        """
        Parameters
        ----------
        spec_type: str
            Spectrum type. Can be 'abs' - absorbance, 'trans' - transmission,
            'raw' - uncorrected (except for dark correction).
        """

        if not self.is_busy():
            if spec_type == 'abs':
                spectrum, timestamp = self._get_absorbance_spectrum_inner(dark_correct,
                    int_trigger)

            elif spec_type == 'trans':
                spectrum, timestamp = self._get_transmission_spectrum_inner(dark_correct,
                    int_trigger)
            else:
                spectrum, timestamp = self._get_spectrum_inner(dark_correct, int_trigger)

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        return timestamp, spectrum

    def _get_spectrum_inner(self, dark_correct, int_trigger):
        spectrum = self._collect_spectrum(int_trigger)
        timestamp = datetime.datetime.now()

        self._add_spectrum_to_history(spectrum, timestamp)

        if dark_correct:
            dark_spectrum = self.get_dark()[1]

            spectrum = self.subtract_spectra(spectrum, dark_spectrum)

        return spectrum, timestamp

    def _get_transmission_spectrum_inner(self, dark_correct, int_trigger):
        spectrum, timestamp = self._get_spectrum_inner(dark_correct, int_trigger)

        ref_spectrum = self.get_reference_spectrum()[1]

        spectrum = self.divide_spectra(spectrum, ref_spectrum)

        self._add_spectrum_to_history(spectrum, timestamp, spec_type='trans')

        return spectrum, timestamp

    def _get_absorbance_spectrum_inner(self, dark_correct, int_trigger):
        spectrum, timestamp = self._get_transmission_spectrum_inner(dark_correct,
            int_trigger)

        spectrum[:,1] = -np.log10(spectrum[:,1])

        self._add_spectrum_to_history(spectrum, timestamp, spec_type='abs')

        return spectrum, timestamp

    def get_spectra_series(self, num_spectra, spec_type='abs', return_q=None,
        delta_t_min=0, dark_correct=True, int_trigger=True):
        if self.is_busy():
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        else:
            logger.info('Collecting a series of {} spectra'.format(num_spectra))
            self._series_thread = threading.Thread(target=self._collect_spectra_series,
                args=(num_spectra,), kwargs={'return_q': return_q,
                'spec_type': spec_type, 'delta_t_min' : delta_t_min,
                'dark_correct' : dark_correct, 'int_trigger' : int_trigger})

            self._series_thread.daemon = True
            self._series_thread.start()

    def _collect_spectra_series(self, num_spectra, return_q=None, spec_type='abs',
        delta_t_min=0, dark_correct=True, int_trigger=True):
        if self.is_busy():
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        else:
            self._series_abort_event.clear()
            self._taking_series = True

            tot_spectrum = 0

            dt_delta_t = datetime.timedelta(seconds=delta_t_min)

            if self._series_abort_event.is_set():
                return

            while tot_spectrum < num_spectra:
                if self._series_abort_event.is_set():
                    break

                logger.debug('Collecting series spectra {}'.format(tot_spectrum+1))

                if spec_type == 'abs':
                    spectrum, timestamp = self._get_absorbance_spectrum_inner(dark_correct,
                        int_trigger)

                elif spec_type == 'trans':
                    spectrum, timestamp = self._get_transmission_spectrum_inner(dark_correct,
                        int_trigger)
                else:
                    spectrum, timestamp = self._get_spectrum_inner(dark_correct,
                        int_trigger)

                if return_q is not None:
                    logger.debug('Returning series spectra {}'.format(tot_spectrum+1))

                    try:
                        return_q.put_nowait([timestamp, spectrum])
                    except:
                        return_q.append([timestamp, spectrum])

                tot_spectrum += 1

                while datetime.datetime.now() - timestamp < dt_delta_t:
                    if self._series_abort_event.is_set():
                        break

                    time.sleep(0.01)

            self._taking_series = False

    def subtract_spectra(self, spectrum1, spectrum2):
        """Return spectrum1 - spectrum2"""
        if np.all(spectrum1[:,0] == spectrum2[:,0]):
            sub_spectrum = np.column_stack((spectrum1[:,0],
                spectrum1[:,1] - spectrum2[:,1]))

        else:
            raise ValueError('spectrum do not have the same wavelength, and so '
                'cannot be subtracted.')

        return sub_spectrum

    def divide_spectra(self, spectrum1, spectrum2):
        """Return spectrum1/spectrum2"""
        if np.all(spectrum1[:,0] == spectrum2[:,0]):
            ratio_spectrum = np.column_stack((spectrum1[:,0],
                spectrum1[:,1]/spectrum2[:,1]))

        else:
            raise ValueError('spectrum do not have the same wavelength, and so '
                'cannot be divided.')

        return ratio_spectrum

    def _add_spectrum_to_history(self, spectrum, timestamp, spec_type='raw'):
        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        history['spectra'].append([timestamp, spectrum])
        history['timestamps'].append(timestamp.timestamp())

        history = self._prune_history(history)

        if spec_type == 'abs':
            self._absorbance_history = history
        elif spec_type == 'trans':
            self._transmission_history = history
        else:
            self._history = history

    def _prune_history(self, history):
        if len(history['timestamps']) > 0:
            now = datetime.datetime.now().timestamp()

            if len(history['timestamps']) == 1:
                if now - history['timestamps'][0] > self._history_length:
                    index = 1
                else:
                    index = 0

            else:
                index = 0

                while (index < len(history['timestamps'])-1
                    and now - history['timestamps'][index] > self._history_length):
                    index += 1

            if index == len(history['timestamps']):
                history['spectra'] = []
                history['timestamps'] = []

            elif index != 0:
                history['spectra'] = history['spectra'][index:]
                history['timestamps'] = history['timestamps'][index:]

        return history

    def get_last_n_spectra(self, n, spec_type='abs'):
        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        return history['spectra'][-n:]

    def get_spectra_in_last_t(self, t, spec_type='abs'):
        """
        Parameters
        ----------

        t: float
            Time in seconds
        """
        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        now = datetime.datetime.now().timestamp()

        index = -1
        while (abs(index) <= len(history['timestamps'])
            and now - history['timestamps'][index] < t):
            index -= 1

        if index == -1 and len(history['timestamps']) > 0:
            if now - history['timestamps'][index] > t:
                ret_spectra = []
            else:
                ret_spectra = history['spectra'][index:]

        elif index == -1 and len(history['timestamps']) == 0:
            ret_spectra = []

        elif abs(index) == len(history['timestamps']):
            ret_spectra = history['spectra']

        else:
            ret_spectra = history['spectra'][index:]

        return ret_spectra

    def get_full_history(self, spec_type='abs'):
        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        return history['spectra']

    def set_history_time(self, t):
        self._history_length = t

        self._prune_history(self._absorbance_history)
        self._prune_history(self._transmission_history)
        self._prune_history(self._history)

    def abort_collection(self):
        self._series_abort_event.set()

class StellarnetUVVis(Spectrometer):
    """
    Stellarnet black comet UV-Vis spectrometer
    """

    def __init__(self, name):

        Spectrometer.__init__(self, name)

        self._x_timing = 3
        self._temp_comp = None
        self._coeffs = None
        self._det_type = None
        self._model = None
        self._device_id = None

        self._external_trigger = False

        self.connect()
        self._get_config()

    def connect(self):
        spec, wav = sn.array_get_spec(0)

        self.spectrometer = spec
        self.wavelength = wav

    def disconnect(self):
        if self.is_busy():
            self.abort_collection()
        self.spectrometer['device'].__del__()

    def set_integration_time(self, int_time, update_dark=True):
        if int_time != self._integration_time:
            self._set_config(int_time, self._scan_avg, self._smoothing,
                self._x_timing)

            self.collect_dark()

    def set_scan_avg(self, num_avgs, update_dark=True):
        if num_avgs != self._scan_avg:
            self._set_config(self._integration_time, num_avgs, self._smoothing,
                self._x_timing)

            self.collect_dark()

    def set_smoothing(self, smooth, update_dark=True):
        if smooth != self._smoothing:
            self._set_config(self._integration_time, self._scan_avg, smooth,
                self._x_timing)

            self.collect_dark()

    def set_xtiming(self, x_timing, update_dark=True):
        if x_timing != self._x_timing:
            self._set_config(self._integration_time, self._scan_avg,
                self._smoothing, x_timing)

            self.collect_dark()

    def lightsource_shutter(self, open):
        pass

    def _collect_spectrum(self, int_trigger):
        self._taking_data = True

        if self._external_trigger and int_trigger:
            trigger_ext = True
            self.set_external_trigger(False)

        else:
            trigger_ext = False

        spectrum = sn.array_spectrum(self.spectrometer, self.wavelength)

        if trigger_ext:
            self.set_external_trigger(True)

        self._taking_data = False

        return spectrum

    def _check_dark_conditions(self, set_dark_conditions=True):
        """
        Checks whether the spectrometer is dark

        Parameters
        ----------
        set_dark_conditions: bool, optional
            If True (default) will attempt to set dark conditions properly

        Returns
        -------
        is_dark: bool
            Whether spectrometer is currently in a dark condition
        """
        return True

    # function defination to set parameter
    def _set_config(self, int_time, num_avgs, smooth, xtiming):
        self._integration_time = int_time
        self._scan_avg = num_avgs
        self._smoothing = smooth
        self._x_timing = xtiming

        self.spectrometer['device'].set_config(int_time=int_time, scans_to_avg=num_avgs,
            x_smooth=smooth, x_timing=xtiming)

        self._collect_spectrum(True)

    def _get_config(self):
        params = self.spectrometer['device'].get_config()

        self._integration_time = params['int_time']
        self._scan_avg = params['scans_to_avg']
        self._smoothing = params['x_smooth']
        self._x_timing = params['x_timing']
        self._temp_comp = params['temp_comp']
        self._coeffs = params['coeffs']
        self._det_type = params['det_type']
        self._model = params['model']
        self._device_id = params['device_id']

    def set_external_trigger(trigger):
        self.ext_trig = trigger
        sn.ext_trig(self.spectrometer, trigger)



if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    # h1.setLevel(logging.INFO)
    # h1.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    spec = StellarnetUVVis('Test')
    spec.collect_dark()
    spec.collect_reference_spectrum()
    # spec.disconnect()
