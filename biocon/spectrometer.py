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
import os

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
        logger.debug('SpectraData: Getting all absorbance values')
        return self.absorbance_values

    def get_absorbance(self, wavelength):
        logger.debug('SpectraData: Getting absorbance at %s', wavelength)
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
        logger.debug('SpectraData: Getting absorbance window')
        return self._absorbance_window

    def set_absorbance_window(self, window):
        logger.debug('SpectraData: Setting absorbance window')
        self._absorbance_window = window
        for wavelength in self.absorbance_values:
            self._calculate_absorbance_range(wavelength)

        self._calculate_all_abs_single_wavelength()

    def save_spectrum(self, name, save_dir, spec_type='abs'):

        fname = os.path.join(save_dir, name)
        logger.debug('SpectraData: Saving to %s', fname)

        h_start = '{}\nWavelength_(nm),'.format(self.timestamp.isoformat())

        if spec_type == 'raw':
            header = h_start + 'Spectrum'
        elif spec_type == 'trans':
            header = h_start + 'Transmission'
        elif spec_type == 'abs':
            header = h_start + 'Absorbance_(Au)'

        np.savetxt(fname, self.get_spectrum(spec_type), delimiter=',',
            header=header)

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

        self._autosave_dir = None
        self._autosave_prefix = None
        self._autosave_raw = False
        self._autosave_trans = False
        self._autosave_abs = True
        self._autosave_on = False


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
            spectrum = self._collect_spectrum_inner(dark_correct, int_trigger)
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

        if self._dark_spectrum is not None:
            dark_spec = self.get_dark()

        if (self._dark_spectrum is None or
            (datetime.datetime.now() - dark_spec.get_timestamp()
            > datetime.timedelta(seconds=dark_time))):
            self.collect_dark()

    def collect_spectrum(self, spec_type='abs', dark_correct=True, int_trigger=True,
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
                spectrum = self._collect_absorbance_spectrum_inner(dark_correct,
                    int_trigger)

            elif spec_type == 'trans':
                spectrum = self._collect_transmission_spectrum_inner(dark_correct,
                    int_trigger)
            else:
                spectrum = self._collect_spectrum_inner(dark_correct, int_trigger)

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        return spectrum

    def _collect_spectrum_inner(self, dark_correct, int_trigger):
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

    def _collect_transmission_spectrum_inner(self, dark_correct, int_trigger):
        logger.debug('Spectrometer %s: Getting transmission spectrum', self.name)
        spectrum = self._collect_spectrum_inner(dark_correct, int_trigger)

        ref_spectrum = self.get_reference_spectrum()

        spectrum.transmission_from_ref(ref_spectrum)

        self._add_spectrum_to_history(spectrum, spec_type='trans')

        return spectrum

    def _collect_absorbance_spectrum_inner(self, dark_correct, int_trigger):
        logger.debug('Spectrometer %s: Getting absorbance spectrum', self.name)
        spectrum = self._collect_transmission_spectrum_inner(dark_correct,
            int_trigger)

        self._add_spectrum_to_history(spectrum, spec_type='abs')

        return spectrum

    def collect_spectra_series(self, num_spectra, spec_type='abs', return_q=None,
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
                    spectrum = self._collect_absorbance_spectrum_inner(dark_correct,
                        int_trigger)

                elif spec_type == 'trans':
                    spectrum = self._collect_transmission_spectrum_inner(dark_correct,
                        int_trigger)
                else:
                    spectrum = self._collect_spectrum_inner(dark_correct,
                        int_trigger)

                if self._autosave_on:
                    s_base = '{}_{:06}'.format(self._autosave_prefix , tot_spectrum+1)

                    if self._autosave_raw:
                        logger.debug('Autosaving raw spectra')
                        s_name = s_base + '_raw.csv'
                        spectrum.save_spectrum(s_name, self._autosave_dir, 'raw')

                    if (self._autosave_trans and
                        (spec_type == 'trans' or spec_type == 'abs')):
                        logger.debug('Autosaving trans spectra')
                        s_name = s_base + '_trans.csv'
                        spectrum.save_spectrum(s_name, self._autosave_dir, 'trans')

                    if self._autosave_abs and spec_type == 'abs':
                        logger.debug('Autosaving abs spectra')
                        s_name = s_base + '.csv'
                        spectrum.save_spectrum(s_name, self._autosave_dir, 'abs')

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

    def get_history_time(self):
        logger.debug('Spectrometer %s: Getting history length', self.name)
        return self._history_length

    def add_absorbance_wavelength(self, wavelength):
        logger.info('Spectrometer %s: Adding absorbance at %s nm', self.name,
            wavelength)
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
        logger.debug('Spectrometer %s: Getting absorbance wavelengths', self.name)
        return list(self._absorbance_wavelengths.keys())

    def remove_absorbance_wavelength(self, wavelength):
        logger.info('Spectrometer %s: Removing absorbance at %s nm', self.name,
            wavelength)
        self._absorbance_wavelengths.pop(wavelength, None)

    def set_absorbance_window(self, window_size):
        logger.info('Spectrometer %s: Setting absorbance window to %s nm',
            self.name, window_size)
        self._absorbance_window = window_size

        for wavelength in self._absorbance_wavelengths:
            self._calculate_absorbance_range(wavelength)

    def get_absorbance_window(self):
        logger.debug('Spectrometer %s: Getting absorbance window', self.name)
        return self._absorbance_window

    def set_autosave_parameters(self, data_dir, prefix, save_raw=False,
        save_trans=False, save_abs=True):
        logger.debug('Spectrometer %s: Setting series autosave parameters: '
            'savedir: %s, prefix: %s, save_raw: %s, save_trans: %s, '
            'save_abs: %s', self.name, data_dir, prefix, save_raw, save_trans,
            save_abs)
        self._autosave_dir = data_dir
        self._autosave_prefix = prefix
        self._autosave_raw = save_raw
        self._autosave_trans = save_trans
        self._autosave_abs = save_abs

    def set_autosave(self, on):
        logger.info('Spectrometer %s: Setting series autosave to %s', self.name,
            on)
        self._autosave_on = on

    def get_autosave(self):
        logger.debug('Spectrometer %s: Getting series autosave', self.name)
        return self._autosave_on

    def get_autosave_parameters(self):
        logger.debug('Spectrometer %s: Getting series autosave parameters',
            self.name)
        return (self._autosave_dir, self._autosave_prefix, self._autosave_raw,
            self._autosave_trans, self._autosave_abs)

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


class UVCommThread(utils.CommManager):

    def __init__(self, name):
        utils.CommManager.__init__(self, name)

        self._commands = {
            'connect'           : self._connect_device,
            'disconnect'        : self._disconnect_device,
            'set_int_time'      : self._set_int_time,
            'set_scan_avg'      : self._set_scan_avg,
            'set_smoothing'     : self._set_smoothing,
            'set_xtiming'       : self._set_xtiming,
            'get_int_time'      : self._get_int_time,
            'get_scan_avg'      : self._get_scan_avg,
            'get_smoothing'     : self._get_smoothing,
            'get_xtiming'       : self._get_xtiming,
            'set_dark'          : self._set_dark,
            'get_dark'          : self._get_dark,
            'collect_dark'      : self._collect_dark,
            'set_ref'           : self._set_ref,
            'get_ref'           : self._get_ref,
            'collect_ref'       : self._collect_ref,
            'collect_spec'      : self._collect_spec,
            'collect_series'    : self._collect_series,
            'get_last_n'        : self._get_last_n_spectra,
            'get_last_t'        : self._get_spectra_in_last_t,
            'get_full_hist'     : self._get_full_history,
            'set_hist_time'     : self._set_history_time,
            'get_hist_time'     : self._get_history_time,
            'add_abs_wav'       : self._add_absorbance_wavelength,
            'get_abs_wav'       : self._get_absorbance_wavelengths,
            'remove_abs_wav'    : self._remove_absorbance_wavelength,
            'set_abs_window'    : self._set_absorbance_window,
            'get_abs_window'    : self._get_absorbance_window,
            'set_autosave_on'   : self._set_autosave_on,
            'get_autosave_on'   : self._get_autosave_on,
            'set_autosave_param': self._set_autosave_params,
            'get_autosave_param': self._get_autosave_params,
        }

        self._connected_devices = OrderedDict()

        self.known_devices = {
            'StellarNet' : StellarnetUVVis,
            }

    def _connect_device(self, name, device_type, **kwargs):
        logger.info("Connecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)

        new_device = self.known_devices[device_type](name, **kwargs)
        new_device.connect()
        self._connected_devices[name] = new_device

        self._return_value((name, 'connected', True), comm_name)

        logger.debug("Device %s connected", name)

    def _disconnect_device(self, name, **kwargs):
        logger.info("Disconnecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices.pop(name, None)
        if device is not None:
            device.disconnect()

        self._return_value((name, 'disconnected', True), comm_name)

        logger.debug("Device %s disconnected", name)

    def _disconnect_device(self, name, **kwargs):
        logger.info("Disconnecting device %s", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.disconnect(**kwargs)

        self._return_value((name, 'disconnected', True), comm_name)

        logger.debug("Device %s disconnected", name)

    def _set_int_time(self, name, val, **kwargs):
        logger.debug("Setting device %s integration time to %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_integration_time(val, **kwargs)

        self._return_value((name, 'set_int_time', True), comm_name)

        logger.debug("Device %s integraiton time set", name)

    def _set_scan_avg(self, name, val, **kwargs):
        logger.debug("Setting device %s scan averages to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_scan_avg(val, **kwargs)

        self._return_value((name, 'set_scan_avg', True), comm_name)

        logger.debug("Device %s scan averages set", name)

    def _set_smoothing(self, name, val, **kwargs):
        logger.debug("Setting device %s smoothing to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_smoothing(val, **kwargs)

        self._return_value((name, 'set_smoothing', True), comm_name)

        logger.debug("Device %s smoothing set", name)

    def _set_xtiming(self, name, val, **kwargs):
        logger.debug("Setting device %s xtiming to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_xtiming(val, **kwargs)

        self._return_value((name, 'set_xtiming', True), comm_name)

        logger.debug("Device %s xtiming set", name)

    def _get_int_time(self, name, **kwargs):
        logger.debug("Getting device %s integration time", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_integration_time(**kwargs)

        self._return_value((name, 'int_time', val), comm_name)

        logger.debug("Device %s integration time is %s s", name, val)

    def _get_scan_avg(self, name, **kwargs):
        logger.debug("Getting device %s scan averages", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_scan_avg(**kwargs)

        self._return_value((name, 'scan_avg', val), comm_name)

        logger.debug("Device %s scan averages: %s", name, val)

    def _get_smoothing(self, name, **kwargs):
        logger.debug("Getting device %s smoothing", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_smoothing(**kwargs)

        self._return_value((name, 'smoothing', val), comm_name)

        logger.debug("Device %s smoothing: %s", name, val)

    def _get_xtiming(self, name, **kwargs):
        logger.debug("Getting device %s xtiming", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_xtiming(**kwargs)

        self._return_value((name, 'xtiming', val), comm_name)

        logger.debug("Device %s xtiming: %s", name, val)

    def _set_dark(self, name, val, **kwargs):
        logger.debug("Setting device %s dark to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_dark(val, **kwargs)

        self._return_value((name, 'set_dark', True), comm_name)

        logger.debug("Device %s dark set", name)

    def _get_dark(self, name, **kwargs):
        logger.debug("Getting device %s dark", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_dark(**kwargs)

        self._return_value((name, 'dark', val), comm_name)

        logger.debug("Device %s dark: %s", name, val)

    def _collect_dark(self, name, **kwargs):
        logger.debug("Collecting device %s dark", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.collect_dark(**kwargs)

        self._return_value((name, 'collect_dark', val), comm_name)

        logger.debug("Device %s dark: %s", name, val)

    def _set_ref(self, name, val, **kwargs):
        logger.debug("Setting device %s ref to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_reference_spectrum(val, **kwargs)

        self._return_value((name, 'set_ref', True), comm_name)

        logger.debug("Device %s ref set", name)

    def _get_ref(self, name, **kwargs):
        logger.debug("Getting device %s ref", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_reference_spectrum(**kwargs)

        self._return_value((name, 'ref', val), comm_name)

        logger.debug("Device %s ref: %s", name, val)

    def _collect_ref(self, name, **kwargs):
        logger.debug("Collecting device %s ref", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.collect_reference_spectrum(**kwargs)

        self._return_value((name, 'collect_ref', val), comm_name)

        logger.debug("Device %s ref: %s", name, val)

    def _collect_spec(self, name, **kwargs):
        logger.debug("Collecting device %s spectrum", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.collect_spectrum(**kwargs)

        self._return_value((name, 'collect_spec', val), comm_name)

        logger.debug("Device %s spectrum: %s", name, val)

    def _collect_series(self, name, val, **kwargs):
        logger.debug("Collecting device %s spectra series of %s spectra", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.collect_spectra_series(val, **kwargs)

        self._return_value((name, 'collect_series', True), comm_name)

        logger.debug("Device %s series started", name)

    def _get_last_n_spectra(self, name, val, **kwargs):
        logger.debug("Getting device %s %s most recent spectra", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        hist = device.get_last_n_spectra(val, **kwargs)

        self._return_value((name, 'get_history', hist), comm_name)

        logger.debug("Device %s history returned", name)

    def _get_spectra_in_last_t(self, name, val, **kwargs):
        logger.debug("Getting device %s spectra in the last %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        hist = device.get_spectra_in_last_t(val, **kwargs)

        self._return_value((name, 'get_history', hist), comm_name)

        logger.debug("Device %s history returned", name)

    def _get_full_history(self, name, **kwargs):
        logger.debug("Getting device %s full spectra history", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_full_history(**kwargs)

        self._return_value((name, 'get_history', val), comm_name)

        logger.debug("Device %s history returned", name)

    def _set_history_time(self, name, val, **kwargs):
        logger.debug("Setting device %s history length to %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_history_time(val, **kwargs)

        self._return_value((name, 'set_history_time', True), comm_name)

        logger.debug("Device %s history length set", name)

    def _get_history_time(self, name, **kwargs):
        logger.debug("Getting device %s history length", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_history_time(**kwargs)

        self._return_value((name, 'get_history_time', val), comm_name)

        logger.debug("Device %s history time: %s s", name, val)

    def _add_absorbance_wavelength(self, name, val, **kwargs):
        logger.debug("Device %s adding absorbance wavelenght %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.add_absorbance_wavelength(val, **kwargs)

        self._return_value((name, 'add_abs_wav', True), comm_name)

        logger.debug("Device %s absorbance wavelenght added", name)

    def _get_absorbance_wavelengths(self, name, **kwargs):
        logger.debug("Getting device %s absorbance wavelengths", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_absorbance_wavelengths(**kwargs)

        self._return_value((name, 'get_abs_wav', val), comm_name)

        logger.debug("Device %s absorbance wavelengths: %s", name, val)

    def _remove_absorbance_wavelength(self, name, val, **kwargs):
        logger.debug("Device %s removing absorbance wavelenght %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.remove_absorbance_wavelength(val, **kwargs)

        self._return_value((name, 'remove_abs_wav', True), comm_name)

        logger.debug("Device %s absorbance wavelength removed", name)

    def _set_absorbance_window(self, name, val, **kwargs):
        logger.debug("Device %s setting absorbance window %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_absorbance_window(val, **kwargs)

        self._return_value((name, 'set_abs_window', True), comm_name)

        logger.debug("Device %s absorbance window added", name)

    def _get_absorbance_window(self, name, **kwargs):
        logger.debug("Getting device %s absorbance window", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_absorbance_window(**kwargs)

        self._return_value((name, 'get_abs_wav', val), comm_name)

        logger.debug("Device %s absorbance window: %s nm", name, val)

    def _set_autosave_on(self, name, val, **kwargs):
        logger.debug("Device %s setting series autosave to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_autosave(val, **kwargs)

        self._return_value((name, 'set_autosave_on', True), comm_name)

        logger.debug("Device %s autosave on set", name)

    def _get_autosave_on(self, name, **kwargs):
        logger.debug("Getting device %s autosave on", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_autosave(**kwargs)

        self._return_value((name, 'get_autosave_on', val), comm_name)

        logger.debug("Device %s autosave on: %s", name, val)

    def _set_autosave_params(self, name, data_dir, prefix, **kwargs):
        logger.debug("Device %s setting series autosave parameters to %s",
            name, val)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        device.set_autosave_parameters(data_dir, prefix, **kwargs)

        self._return_value((name, 'set_autosave_params', True), comm_name)

        logger.debug("Device %s autosave parameters set", name)

    def _get_autosave_params(self, name, **kwargs):
        logger.debug("Getting device %s autosave parameters", name)

        comm_name = kwargs.pop('comm_name', None)

        device = self._connected_devices[name]
        val = device.get_autosave_parameters(**kwargs)

        self._return_value((name, 'get_autosave_params', val), comm_name)

        logger.debug("Device %s autosave parameters: %s", name, val)

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

    # spec = StellarnetUVVis('Test')
    # spec.collect_dark()
    # spec.collect_reference_spectrum()
    # spec.disconnect()

    comm_thread = UVCommThread('Test')
    comm_thread.start()

    # cmd_q = deque()
    # ret_q = deque()
    # status_q = deque()

    # comm_thread.add_new_communication('test_com', cmd_q, ret_q, status_q)

    # connect_cmd = ['connect', ['Test2', 'StellarNet'], {}]
    # cmd_q.append(connect_cmd)

    # disconnect_cmd = ['disconnect', ['Test2'], {}]
    # cmd_q.append(disconnect_cmd)

    # set_int_time_cmd = ['set_int_time', ['Test2', 0.01], {}]
    # cmd_q.append(set_int_time_cmd)

    # set_scan_avg_cmd = ['set_scan_avg', ['Test2', 1], {}]
    # cmd_q.append(set_scan_avg_cmd)

    # set_smoothing_cmd = ['set_smoothing', ['Test2', 0], {}]
    # cmd_q.append(set_smoothing_cmd)

    # set_xtiming_cmd = ['set_xtiming', ['Test2', 3], {}]
    # cmd_q.append(set_xtiming_cmd)

    # get_int_time_cmd = ['get_int_time', ['Test2'], {}]
    # cmd_q.append(get_int_time_cmd)

    # get_scan_avg_cmd = ['get_scan_avg', ['Test2'], {}]
    # cmd_q.append(get_scan_avg_cmd)

    # get_smoothing_cmd = ['get_smoothing', ['Test2'], {}]
    # cmd_q.append(get_smoothing_cmd)

    # get_xtiming_cmd = ['get_xtiming', ['Test2'], {}]
    # cmd_q.append(get_xtiming_cmd)


    # collect_dark_cmd = ['collect_dark', ['Test2'], {}]
    # cmd_q.append(collect_dark_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_dark_cmd = ['get_dark', ['Test2'], {}]
    # cmd_q.append(get_dark_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # dark = ret_q.pop()[2]

    # set_dark_cmd = ['set_dark', ['Test2', dark], {}]
    # cmd_q.append(set_dark_cmd)



    # collect_ref_cmd = ['collect_ref', ['Test2'], {}]
    # cmd_q.append(collect_ref_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_ref_cmd = ['get_ref', ['Test2'], {}]
    # cmd_q.append(get_ref_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # ref = ret_q.pop()[2]

    # set_ref_cmd = ['set_ref', ['Test2', ref], {}]
    # cmd_q.append(set_ref_cmd)


    # start_count = len(ret_q)

    # collect_spec_cmd = ['collect_spec', ['Test2'], {}]
    # cmd_q.append(collect_spec_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # s1 = ret_q.pop()[2]

    # start_count = len(ret_q)

    # collect_spec_cmd = ['collect_spec', ['Test2'], {'spec_type':'raw'}]
    # cmd_q.append(collect_spec_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # s1 = ret_q.pop()[2]


    # start_count = len(ret_q)

    # collect_series_cmd = ['collect_series', ['Test2', 5], {}]
    # cmd_q.append(collect_series_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # s1 = ret_q.pop()[2]

    # start_count = len(ret_q)

    # collect_series_cmd = ['collect_series', ['Test2', 5], {'spec_type':'raw'}]
    # cmd_q.append(collect_series_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # s1 = ret_q.pop()[2]


    # start_count = len(ret_q)

    # get_last_n_cmd = ['get_last_n', ['Test2',5], {}]
    # cmd_q.append(get_last_n_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # history = ret_q.pop()[2]

    # start_count = len(ret_q)

    # get_last_t_cmd = ['get_last_t', ['Test2',300], {}]
    # cmd_q.append(get_last_t_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # history = ret_q.pop()[2]

    # start_count = len(ret_q)

    # get_full_history_cmd = ['get_full_hist', ['Test2'], {}]
    # cmd_q.append(get_full_history_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # history = ret_q.pop()[2]

    # start_count = len(ret_q)

    # get_hist_time_cmd = ['get_hist_time', ['Test2'], {}]
    # cmd_q.append(get_hist_time_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # history_length = ret_q.pop()[2]

    # set_hist_time_cmd = ['set_hist_time', ['Test2', 60*60], {}]
    # cmd_q.append(set_hist_time_cmd)


    # add_abs_wav_cmd = ['add_abs_wav', ['Test2', 280], {}]
    # cmd_q.append(add_abs_wav_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_abs_wav_cmd = ['get_abs_wav', ['Test2'], {}]
    # cmd_q.append(get_abs_wav_cmd)

    # time.sleep(0.5)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # val = ret_q.pop()[2]

    # remove_abs_wav_cmd = ['remove_abs_wav', ['Test2', 280], {}]
    # cmd_q.append(remove_abs_wav_cmd)

    # set_abs_window_cmd = ['set_abs_window', ['Test2', 1], {}]
    # cmd_q.append(set_abs_window_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_abs_window_cmd = ['get_abs_window', ['Test2'], {}]
    # cmd_q.append(get_abs_window_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # val = ret_q.pop()[2]


    # set_autosave_on_cmd = ['set_autosave_on', ['Test2', True], {}]
    # cmd_q.append(set_autosave_on_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_autosave_on_cmd = ['get_autosave_on', ['Test2'], {}]
    # cmd_q.append(get_autosave_on_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # val = ret_q.pop()[2]

    # save_dir = '/Users/jessehopkins/Desktop/projects/spectrometer/test_save'

    # set_autosave_param_cmd = ['set_autosave_param', ['Test2', save_dir, 'test_thread'], {}]
    # cmd_q.append(set_autosave_param_cmd)

    # time.sleep(0.5)

    # start_count = len(ret_q)

    # get_autosave_param_cmd = ['get_autosave_param', ['Test2'], {}]
    # cmd_q.append(get_autosave_param_cmd)

    # while len(ret_q) == start_count:
    #     time.sleep(0.1)

    # val = ret_q.pop()[2]


    # cmd_q2 = deque()
    # ret_q2 = deque()
    # status_q2 = deque()

    # comm_thread.add_new_communication('test_com2', cmd_q2, ret_q2, status_q2)

    # get_int_time_cmd = ['get_int_time', ['Test2'], {}]
    # cmd_q.append(get_int_time_cmd)

    # get_scan_avg_cmd = ['get_scan_avg', ['Test2', 1], {}]
    # cmd_q2.append(get_scan_avg_cmd)

    # get_int_status_cmd = ['get_int_time', ['Test2',], {}]
    # comm_thread.add_status_cmd(get_int_status_cmd, 10)

    """
    To do:
    Figure out how we'll be controling the shutter on the light source
    Make simple GUI
    """
