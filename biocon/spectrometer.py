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
sys.path.append('/Users/jessehopkins/Desktop/projects/spectrometer/MAC_64b_python3')#add the path of the stellarnet_demo.py
import stellarnet_driver3 as sn

import utils


class SpectraData(object):
    """
    Data class for spectra
    """

    def __init__(self, spectrum, timestamp, spec_type='raw',
        absorbance_window=1, absorbance_wavelengths={}):
        logger.debug('Creating SpectraData with %s spectrum', spec_type)

        self.timestamp = timestamp
        self.wavelength = spectrum[:,0]

        self.spectrum = None
        self.trans_spectrum = None
        self.abs_spectrum = None

        self._absorbance_wavelengths = absorbance_wavelengths
        self._absorbance_window = absorbance_window
        self.absorbance_values = {}

        if spec_type == 'raw':
            self.spectrum = spectrum[:,1]
        elif spec_type == 'trans':
            self.trans_spectrum = spectrum[:,1]
        elif spec_type == 'abs':
            self.abs_spectrum = spectrum[:,1]

            self._calculate_absorbances()

    def get_timestamp(self):
        logger.debug('SpectraData: Getting timestamp')
        return self.timestamp

    def get_wavelength(self):
        logger.debug('SpectraData: Getting wavelength')
        return self.wavelength

    def get_spectrum(self, spec_type='raw'):
        logger.debug('SpectraData: Getting %s spectrum', spec_type)

        if spec_type == 'raw':
            spec = self.spectrum
        elif spec_type == 'trans':
            spec = self.trans_spectrum
        elif spec_type == 'abs':
            spec = self.abs_spectrum

        spectrum = np.column_stack((self.wavelength, spec))

        return spectrum

    def set_spectrum(self, spectrum, spec_type='raw'):
        logger.debug('SpectraData: Setting %s spectrum', spec_type)

        if spec_type == 'raw':
            self.spectrum = spectrum[:,1]

        elif spec_type == 'trans':
            self.trans_spectrum = spectrum[:,1]
            self.calc_abs()

        elif spec_type == 'abs':
            self.abs_spectrum = spectrum[:,1]
            self._calculate_all_abs_single_wavelength()

    def dark_correct(self, dark_spectrum):
        logger.debug('SpectraData: Dark correcting spectrum')
        bkg = dark_spectrum.get_spectrum()

        self.spectrum = self.spectrum - bkg[:,1]

    def transmission_from_ref(self, ref_spectrum):
        logger.debug('SpectraData: Calculating transmission and absorbance')

        bkg = ref_spectrum.get_spectrum()

        self.trans_spectrum = self.spectrum/bkg[:,1]

        self.calc_abs()

    def calc_abs(self):
        logger.debug('SpectraData: Calculating absorbance')

        self.abs_spectrum = -np.log10(self.trans_spectrum)

        self._calculate_all_abs_single_wavelength()

    def _calculate_all_abs_single_wavelength(self):
        for wvl in self._absorbance_wavelengths:
            self._calculate_abs_single_wavelength(wvl)

    def _calculate_abs_single_wavelength(self, wavelength):
        start = self._absorbance_wavelengths[wavelength]['start']
        end = self._absorbance_wavelengths[wavelength]['end']

        abs_val = np.mean(self.abs_spectrum[start:end+1])

        self.absorbance_values[wavelength] = abs_val

    def get_all_absorbances(self):
        return self.absorbance_values

    def get_absorbance(self, wavelength):
        if wavelength < self.wavelength[0] or wavelength > self.wavelength[-1]:
            raise RuntimeError('Wavelength is outside of measured range.')

        if wavelength not in self.absorbance_values:
            self._calculate_absorbance_range(wavelength)
            self._calculate_abs_single_wavelength(wavelength)

        abs_val = self.absorbance_values[wavelength]

        return abs_val

    def _calculate_absorbance_range(self, wvl):
        wvl_start = wvl - self._absorbance_window/2
        wvl_end = wvl + self._absorbance_window/2

        _, start_idx = utils.find_closest(wvl_start, self.wavelength)
        _, end_idx = utils.find_closest(wvl_end, self.wavelength)

        self._absorbance_wavelengths[wvl] = {'start': start_idx, 'end': end_idx}

    def get_absorbance_window(self):
        return self._absorbance_window

    def set_absorbance_window(self, window):
        self._absorbance_window = window
        for wavelength in self.absorbance_values:
            self._calculate_absorbance_range(wavelength)

        self._calculate_all_abs_single_wavelength()

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
        logger.info('Creating spectrometer %s', name)
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

        self._absorbance_window = 1 #window of lambdas to average for absorbance at particular wavelengths
        self._absorbance_wavelengths = {}

        self.wavelength = None #Wavelength array as returned by spectrometer


    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.name)

    def __str__(self):
        return '{} {}'.format(self.__class__.__name__, self.name)

    def connect(self):
        logger.info('Spectrometer %s: Connecting', self.name)

    def disconnect(self):
        logger.info('Spectrometer %s: Disconnecting', self.name)

    def set_integration_time(self, int_time, update_dark=True):
        logger.info('Spectrometer %s: Setting integration time to %s s',
            self.name, int_time)

    def set_scan_avg(self, num_avgs, update_dark=True):
        logger.info('Spectrometer %s: Setting number of scans to average for '
            'each collected spectra to %s', self.name, num_avgs)

    def set_smoothing(self, smooth, update_dark=True):
        logger.info('Spectrometer %s: Setting smoothing to %s', self.name,
            smooth)

    def lightsource_shutter(self, open):
        logger.debug('Spectrometer %s: Opening light source shutter: %s',
            self.name, open)

    def _collect_spectrum(self, int_trigger):
        logger.debug('Spectrometer %s: Collecting spectrum', self.name)

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
        logger.debug('Spectrometer %s: Checking dark conditions', self.name)

    def is_busy(self):
        busy =self._taking_data or self._taking_series
        logger.debug('Spectrometer %s: Busy: %s', self.name, busy)

        return

    def get_integration_time(self):
        logger.debug('Spectrometer %s: Integration time: %s s', self.name,
            self._integration_time)

        return self._integration_time

    def get_scan_avg(self):
        logger.debug('Spectrometer %s: Scans to average: %s', self.name,
            self._scan_avg)

        return self._scan_avg

    def get_smoothing(self):
        logger.debug('Spectrometer %s: Smoothing: %s', self.name, self._smoothing)

        return self._smoothing

    def set_dark(self, spectrum):
        logger.debug('Spectrometer %s: Setting dark spectrum', self.name)

        self._dark_spectrum = spectrum

    def get_dark(self):
        logger.debug('Spectrometer %s: Getting dark spectrum', self.name)

        if self._dark_spectrum is None:
            raise RuntimeError('No dark spectrum')

        return self._dark_spectrum

    def collect_dark(self, averages=1, set_dark_conditions=True):
        logger.info('Spectrometer %s: Collecting dark spectrum', self.name)
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

                avg_spec = SpectraData(avg_spectrum, avg_timestamp,
                    absorbance_window=self._absorbance_window,
                    absorbance_wavelengths=self._absorbance_wavelengths)

                self.set_dark(avg_spec)
            else:
                raise RuntimeError('Spectrometer is not in dark conditions, so '
                    'a dark reference spectrum could not be collected.')

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

    def set_reference_spectrum(self, spectrum):
        logger.debug('Spectrometer %s: Setting reference spectrum', self.name)

        self._reference_spectrum = spectrum

    def get_reference_spectrum(self):
        logger.debug('Spectrometer %s: Getting reference spectrum', self.name)

        if self._reference_spectrum is None:
            raise RuntimeError('No reference spectrum')

        return self._reference_spectrum

    def collect_reference_spectrum(self, averages=1, dark_correct=True,
        int_trigger=True, auto_dark=True, dark_time=60*60):
        if not self.is_busy():
            if auto_dark:
                self._auto_dark(dark_time)

            self._collect_reference_spectrum_inner(averages, dark_correct, int_trigger)

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')



    def _collect_reference_spectrum_inner(self, averages=1, dark_correct=True,
        int_trigger=True):
        logger.info('Spectrometer %s: Collecting reference spectrum', self.name)

        all_spectra = []

        for i in range(averages):
            spectrum = self._get_spectrum_inner(dark_correct, int_trigger)
            all_spectra.append(spectrum.get_spectrum())

            if i == 0:
                initial_timestamp = spectrum.get_timestamp()

        if averages > 1:
            avg_timestamp = initial_timestamp + (spectrum.get_timestamp()-initial_timestamp)/2
            avg_spectrum = np.mean(all_spectra, axis=0)
        else:
            avg_timestamp = initial_timestamp
            avg_spectrum = all_spectra[0]

        avg_spec = SpectraData(avg_spectrum, avg_timestamp,
            absorbance_window=self._absorbance_window,
            absorbance_wavelengths=self._absorbance_wavelengths)

        self.set_reference_spectrum(avg_spec)

    def _auto_dark(self, dark_time):
        dark_spec = self.get_dark()

        if (datetime.datetime.now() - dark_spec.get_timestamp()
            > datetime.timedelta(seconds=dark_time)):
            self.collect_dark()

    def get_spectrum(self, spec_type='abs', dark_correct=True, int_trigger=True,
        auto_dark=True, dark_time=60*60):
        """
        Parameters
        ----------
        spec_type: str
            Spectrum type. Can be 'abs' - absorbance, 'trans' - transmission,
            'raw' - uncorrected (except for dark correction).
        """

        if not self.is_busy():
            if auto_dark:
                self._auto_dark(dark_time)

            logger.info('Spectrometer %s: Collecting spectrum', self.name)

            if spec_type == 'abs':
                spectrum = self._get_absorbance_spectrum_inner(dark_correct,
                    int_trigger)

            elif spec_type == 'trans':
                spectrum = self._get_transmission_spectrum_inner(dark_correct,
                    int_trigger)
            else:
                spectrum = self._get_spectrum_inner(dark_correct, int_trigger)

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        return spectrum

    def _get_spectrum_inner(self, dark_correct, int_trigger):
        logger.debug('Spectrometer %s: Getting raw spectrum', self.name)
        spectrum = self._collect_spectrum(int_trigger)
        timestamp = datetime.datetime.now()

        spectrum = SpectraData(spectrum, timestamp,
            absorbance_window=self._absorbance_window,
            absorbance_wavelengths=self._absorbance_wavelengths)

        if dark_correct:
            dark_spectrum = self.get_dark()

            spectrum.dark_correct(dark_spectrum)

        self._add_spectrum_to_history(spectrum)

        return spectrum

    def _get_transmission_spectrum_inner(self, dark_correct, int_trigger):
        logger.debug('Spectrometer %s: Getting transmission spectrum', self.name)
        spectrum = self._get_spectrum_inner(dark_correct, int_trigger)

        ref_spectrum = self.get_reference_spectrum()

        spectrum.transmission_from_ref(ref_spectrum)

        self._add_spectrum_to_history(spectrum, spec_type='trans')

        return spectrum

    def _get_absorbance_spectrum_inner(self, dark_correct, int_trigger):
        logger.debug('Spectrometer %s: Getting absorbance spectrum', self.name)
        spectrum = self._get_transmission_spectrum_inner(dark_correct,
            int_trigger)

        self._add_spectrum_to_history(spectrum, spec_type='abs')

        return spectrum

    def get_spectra_series(self, num_spectra, spec_type='abs', return_q=None,
        delta_t_min=0, dark_correct=True, int_trigger=True, auto_dark=True,
        dark_time=60*60, take_ref=True, ref_avgs=1):
        if self.is_busy():
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        else:
            logger.info('Spectrometer %s: Collecting a series of %s spectra',
                self.name, num_spectra)
            self._series_thread = threading.Thread(target=self._collect_spectra_series,
                args=(num_spectra,), kwargs={'return_q': return_q,
                'spec_type': spec_type, 'delta_t_min' : delta_t_min,
                'dark_correct' : dark_correct, 'int_trigger' : int_trigger,
                'auto_dark' : auto_dark, 'dark_time' : dark_time,
                'take_ref' : take_ref, 'ref_avgs' : ref_avgs,})

            self._series_thread.daemon = True
            self._series_thread.start()

    def _collect_spectra_series(self, num_spectra, return_q=None, spec_type='abs',
        delta_t_min=0, dark_correct=True, int_trigger=True, auto_dark=True,
        dark_time=60*60, take_ref=True, ref_avgs=1):
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

            if auto_dark:
                self._auto_dark(dark_time)

            if take_ref:
                self._collect_reference_spectrum_inner(ref_avgs)

            while tot_spectrum < num_spectra:
                if self._series_abort_event.is_set():
                    break

                logger.debug('Spectrometer %s: Collecting series spectra %s',
                    self.name, tot_spectrum+1)

                if spec_type == 'abs':
                    spectrum = self._get_absorbance_spectrum_inner(dark_correct,
                        int_trigger)

                elif spec_type == 'trans':
                    spectrum = self._get_transmission_spectrum_inner(dark_correct,
                        int_trigger)
                else:
                    spectrum = self._get_spectrum_inner(dark_correct,
                        int_trigger)

                if return_q is not None:
                    logger.debug('Spectrometer %s: Returning series spectra %s',
                        self.name, tot_spectrum+1)

                    try:
                        return_q.put_nowait(spectrum)
                    except:
                        return_q.append(spectrum)

                tot_spectrum += 1

                while datetime.datetime.now() - spectrum.get_timestamp() < dt_delta_t:
                    if self._series_abort_event.is_set():
                        break

                    time.sleep(0.01)

            self._taking_series = False

            logger.info('Spectrometer %s: Finished Collecting a series of '
                '%s spectra', self.name, num_spectra)

    def subtract_spectra(self, spectrum1, spectrum2, spec_type='raw'):
        """Return spectrum1 - spectrum2"""
        logger.debug('Spectrometer %s: Subtracting spectra')

        spec1 = spectrum1.get_spectrum(spec_type)
        spec2 = spectrum2.get_spectrum(spec_type)

        if np.all(spec1[:,0] == spec2[:,0]):
            sub_spectrum = np.column_stack((spec1[:,0],
                spec1[:,1] - spec2[:,1]))

        else:
            raise ValueError('spectrum do not have the same wavelength, and so '
                'cannot be subtracted.')

        return sub_spectrum

    def divide_spectra(self, spectrum1, spectrum2):
        """Return spectrum1/spectrum2"""
        logger.debug('Spectrometer %s: Dividing spectra')

        spec1 = spectrum1.get_spectrum(spec_type)
        spec2 = spectrum2.get_spectrum(spec_type)

        if np.all(spec1[:,0] == spec2[:,0]):
            ratio_spectrum = np.column_stack((spec1[:,0],
                spec1[:,1]/spec2[:,1]))

        else:
            raise ValueError('spectrum do not have the same wavelength, and so '
                'cannot be divided.')

        return ratio_spectrum

    def _add_spectrum_to_history(self, spectrum, spec_type='raw'):
        logger.debug('Spectrometer %s: Adding %s spectrum to history',
            self.name, spec_type)

        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        history['spectra'].append(spectrum)
        history['timestamps'].append(spectrum.get_timestamp().timestamp())

        history = self._prune_history(history)

        if spec_type == 'abs':
            self._absorbance_history = history
        elif spec_type == 'trans':
            self._transmission_history = history
        else:
            self._history = history

    def _prune_history(self, history):
        logger.debug('Spectrometer %s: Pruning history', self.name)

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
        logger.debug('Spectrometer %s: Getting last %s %s spectra', self.name,
            n, spec_type)

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
        logger.debug('Spectrometer %s: Getting last %s s of %s spectra',
            self.name, t, spec_type)

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
        logger.debug('Spectrometer %s: Getting full history of %s spectra',
            self.name, spec_type)

        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        return history['spectra']

    def set_history_time(self, t):
        logger.debug('Spectrometer %s: Setting history time to %s', self.name, t)

        self._history_length = t

        self._prune_history(self._absorbance_history)
        self._prune_history(self._transmission_history)
        self._prune_history(self._history)

    def add_absorbance_wavelength(self, wavelength):
        if wavelength < self.wavelength[0] or wavelength > self.wavelength[-1]:
            raise RuntimeError('Wavelength is outside of measured range.')

        self._calculate_absorbance_range(wavelength)

    def _calculate_absorbance_range(self, wvl):
        wvl_start = wvl - self._absorbance_window/2
        wvl_end = wvl + self._absorbance_window/2

        _, start_idx = utils.find_closest(wvl_start, self.wavelength)
        _, end_idx = utils.find_closest(wvl_end, self.wavelength)

        self._absorbance_wavelengths[wvl] = {'start': start_idx, 'end': end_idx}

    def get_absorbance_wavelengths(self):
        return list(self._absorbance_wavelengths.keys())

    def remove_absorbance_wavelength(self, wavelength):
        self._absorbance_wavelengths.pop(wavelength, None)

    def set_absorbance_window(self, window_size):
        self._absorbance_window = window_size

        for wavelength in self._absorbance_wavelengths:
            self._calculate_absorbance_range(wavelength)

    def get_absorbance_window(self):
        return self._absorbance_window

    def abort_collection(self):
        logger.info('Spectrometer %s: Aborting collection', self.name)
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
        logger.info('Spectrometer %s: Connecting', self.name)

        spec, wav = sn.array_get_spec(0)

        self.spectrometer = spec
        self.wav = wav

        self.wavelength = self.wav.reshape(self.wav.shape[0])

    def disconnect(self):
        logger.info('Spectrometer %s: Disconnecting', self.name)

        if self.is_busy():
            self.abort_collection()
        self.spectrometer['device'].__del__()

    def set_integration_time(self, int_time, update_dark=True):
        logger.info('Spectrometer %s: Setting integration time to %s s',
            self.name, int_time)

        if int_time != self._integration_time:
            self._set_config(int_time, self._scan_avg, self._smoothing,
                self._x_timing)

            self.collect_dark()

    def set_scan_avg(self, num_avgs, update_dark=True):
        logger.info('Spectrometer %s: Setting number of scans to average for '
            'each collected spectra to %s', self.name, num_avgs)

        if num_avgs != self._scan_avg:
            self._set_config(self._integration_time/1000, num_avgs,
                self._smoothing, self._x_timing)

            self.collect_dark()

    def set_smoothing(self, smooth, update_dark=True):
        logger.info('Spectrometer %s: Setting smoothing to %s', self.name,
            smooth)

        if smooth != self._smoothing:
            self._set_config(self._integration_time, self._scan_avg, smooth,
                self._x_timing)

            self.collect_dark()

    def set_xtiming(self, x_timing, update_dark=True):
        logger.info('Spectrometer %s: Setting x timing to %s', self.name,
            x_timing)

        if x_timing != self._x_timing:
            self._set_config(self._integration_time, self._scan_avg,
                self._smoothing, x_timing)

            self.collect_dark()

    def get_xtiming(self):
        logger.info('Spectrometer %s: X timing: %s', self.name, self._x_timing)

        return self._x_timing

    def lightsource_shutter(self, open):
        logger.debug('Spectrometer %s: Opening light source shutter: %s',
            self.name, open)

    def _collect_spectrum(self, int_trigger):
        logger.debug('Spectrometer %s: Collecting spectrum', self.name)
        self._taking_data = True

        if self._external_trigger and int_trigger:
            trigger_ext = True
            self.set_external_trigger(False)

        else:
            trigger_ext = False

        spectrum = sn.array_spectrum(self.spectrometer, self.wav)

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
        logger.debug('Spectrometer %s: Checking dark conditions', self.name)
        return True

    # function defination to set parameter
    def _set_config(self, int_time, num_avgs, smooth, xtiming):
        int_time = round(int_time*1000)
        self._integration_time = int_time/1000
        self._scan_avg = num_avgs
        self._smoothing = smooth
        self._x_timing = xtiming

        self.spectrometer['device'].set_config(int_time=int_time, scans_to_avg=num_avgs,
            x_smooth=smooth, x_timing=xtiming)

        self._collect_spectrum(True)

    def _get_config(self):
        params = self.spectrometer['device'].get_config()

        self._integration_time = params['int_time']/1000
        self._scan_avg = params['scans_to_avg']
        self._smoothing = params['x_smooth']
        self._x_timing = params['x_timing']
        self._temp_comp = params['temp_comp']
        self._coeffs = params['coeffs']
        self._det_type = params['det_type']
        self._model = params['model']
        self._device_id = params['device_id']

    def set_external_trigger(self, trigger):
        self.ext_trig = trigger
        sn.ext_trig(self.spectrometer, trigger)



if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    spec = StellarnetUVVis('Test')
    spec.collect_dark()
    spec.collect_reference_spectrum()
    # spec.disconnect()

    """
    To do:
    Figure out how we'll be controling the shutter on the light source
    Make communication object that multicasts results, accepts commands from local
        and remote sources
    Make simple GUI
    """
