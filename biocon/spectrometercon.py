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
import wx
try:
    import epics
except ImportError:
    pass
import matplotlib
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
from matplotlib.figure import Figure

matplotlib.rcParams['backend'] = 'WxAgg'

try:
    # Uses stellarnet python driver, available from the manufacturer
    sys.path.append('C:\\Users\\biocat\\Stellarnet\\stellarnet_driverLibs')#add the path of the stellarnet_demo.py
    import stellarnet_driver3 as sn
except ImportError:
    traceback.print_exc()
    sn = None
    pass

import client
import utils


class SpectraData(object):
    """
    Data class for spectra
    """

    def __init__(self, spectrum, timestamp, spec_type='raw',
        absorbance_window=1, absorbance_wavelengths={}, wl_range_idx=[]):
        logger.debug('Creating SpectraData with %s spectrum', spec_type)

        if len(wl_range_idx) > 0:
            spectrum = spectrum[wl_range_idx[0]:wl_range_idx[1]+1]

        self.timestamp = timestamp
        self.wavelength = spectrum[:,0]

        self._raw_spectrum = None
        self.spectrum = None
        self.trans_spectrum = None
        self.abs_spectrum = None

        self._absorbance_wavelengths = absorbance_wavelengths
        self._absorbance_window = absorbance_window
        self.absorbance_values = {}

        if spec_type == 'raw':
            self._raw_spectrum = spectrum[:,1]
            self.spectrum = spectrum[:,1]
        elif spec_type == 'trans':
            self.trans_spectrum = spectrum[:,1]
        elif spec_type == 'abs':
            self.abs_spectrum = spectrum[:,1]
            self._calculate_all_abs_single_wavelength()

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

        if spec is not None:
            spectrum = np.column_stack((self.wavelength, spec))
        else:
            spectrum = None

        return spectrum

    def set_spectrum(self, spectrum, spec_type='raw'):
        logger.debug('SpectraData: Setting %s spectrum', spec_type)

        if spec_type == 'raw':
            self._raw_spectrum = spectrum[:,1]
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

    def drift_correct(self, ref_spectrum, window):
        logger.debug('SpectraData: Drift correcting spectrum')
        bkg = ref_spectrum.get_spectrum()

        wstart = window[0]
        wend = window[1]

        _, start_idx = utils.find_closest(window[0], self.wavelength)
        _, end_idx = utils.find_closest(window[1], self.wavelength)

        ref_avg = np.mean(bkg[start_idx:end_idx+1,1])
        spec_avg = np.mean(self.spectrum[start_idx:end_idx+1])

        self.spectrum = self.spectrum*ref_avg/spec_avg

    def transmission_from_ref(self, ref_spectrum):
        logger.debug('SpectraData: Calculating transmission and absorbance')

        bkg = ref_spectrum.get_spectrum()

        self.trans_spectrum = self.spectrum/bkg[:,1]

        self.calc_abs()

    def calc_abs(self):
        logger.debug('SpectraData: Calculating absorbance')

        self.abs_spectrum = -np.log10(self.trans_spectrum)*1000

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

        name, _ = os.path.splitext(name)
        fname = os.path.join(save_dir, '{}.csv'.format(name))
        logger.debug('SpectraData: Saving to %s', fname)

        h_start = '{}\nWavelength_(nm),'.format(self.timestamp.isoformat())

        if spec_type == 'raw':
            header = h_start + 'Spectrum'
        elif spec_type == 'trans':
            header = h_start + 'Transmission'
        elif spec_type == 'abs':
            header = h_start + 'Absorbance_(mAu)'

        try:
            np.savetxt(fname, self.get_spectrum(spec_type), delimiter=',',
                header=header)
        except Exception:
            pass

class Spectrometer(object):

    def __init__(self, name, device, history_time=60*60*24):
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
        self.device = device

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
        self.series_ready_event = threading.Event()
        self._autosave_queue = deque()
        self._stop_autosave_event = threading.Event()
        self._autosave_thread = threading.Thread(target=self._series_autosave_thread)
        self._autosave_thread.daemon = True
        self._autosave_thread.start()

        self._integration_time = 1
        self._scan_avg = 1
        self._smoothing = 0

        self._drift_window = [None, None]

        self._absorbance_window = 1 #window of lambdas to average for absorbance at particular wavelengths
        self._absorbance_wavelengths = {}

        self.wavelength = None #Wavelength array as returned by spectrometer

        # Sets min and max wavelengths for the spectrometer, must be within
        # the measured range of the spectrometer
        self._wavelength_range = [None, None]
        self._wavelength_range_idx = [None, None]

        self._do_analog_out = True
        self._analog_out_v_max = 10.
        self._analog_out_au_max = 10000.
        self._analog_out_wavelengths = {}

        self._spectrometer_lock = threading.RLock()

        self._live_update = False
        self._live_update_period = 1
        self._live_update_evt = threading.Event()
        self._live_update_stop = threading.Event()
        self._live_update_paused = threading.Event()
        self._live_update_thread = threading.Thread(target=self._live_update_spectra)
        self._live_update_thread.daemon = True
        self._live_update_thread.start()


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
        if self._live_update and not self._taking_series:
            self._live_update_evt.set()

        return self.connected

    def disconnect(self):
        logger.info('Spectrometer %s: Disconnecting', self.name)
        self._stop_autosave_event.set()
        self._live_update_stop.set()
        self._live_update_thread.join(5)

    def set_integration_time(self, int_time, update_dark=True):
        logger.info('Spectrometer %s: Setting integration time to %s s',
            self.name, int_time)

    def set_scan_avg(self, num_avgs, update_dark=True):
        logger.info('Spectrometer %s: Setting number of scans to average for '
            'each collected spectra to %s', self.name, num_avgs)

    def set_smoothing(self, smooth, update_dark=True):
        logger.info('Spectrometer %s: Setting smoothing to %s', self.name,
            smooth)

    def set_lightsource_shutter(self, set_open):
        logger.debug('Spectrometer %s: Opening light source shutter: %s',
            self.name, set_open)

    def get_lightsource_shutter(self):
        logger.debug('Spectrometer %s: Shutter open: %s',
            self.name, status)

    def set_lightsource_uv_lamp(self, set_on):
        logger.debug('Spectrometer %s: Setting UV lamp: %s',
            self.name, set_on)

    def get_lightsource_uv_lamp(self):
        logger.debug('Spectrometer %s: UV lamp on: %s',
            self.name, status)

    def set_lightsource_vis_lamp(self, set_on):
        logger.debug('Spectrometer %s: Setting Vis lamp: %s',
            self.name, set_on)

    def get_lightsource_vis_lamp(self):
        logger.debug('Spectrometer %s: Vis lamp on: %s',
            self.name, status)

    def _collect_spectrum(self, int_trigger):
        logger.debug('Spectrometer %s: Collecting spectrum', self.name)
        with self._spectrometer_lock:
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
        logger.debug('Spectrometer %s: Checking dark conditions', self.name)

    def _check_light_conditions(self, set_light_conditions=True):
        """
        Checks whether the spectrometer is light

        Parameters
        ----------
        set_light_conditions: bool, optional
            If True (default) will attempt to set light conditions properly

        Returns
        -------
        is_light: bool
            Whether spectrometer is currently in a light condition
        """
        logger.debug('Spectrometer %s: Checking light conditions', self.name)

    def _set_analog_output(self, output, val):
        """
        Sets the analog output value, used for integration with external
        equipment such as the the Wyatt MALS/DLS
        """
        pass

    def _get_ext_trig_in(self):
        """
        Gets the value of the external trigger in (for running with MALS).
        Should return True when it is high/starting the series.
        """

    def is_busy(self):
        busy =self._taking_data or self._taking_series
        # logger.debug('Spectrometer %s: Busy: %s', self.name, busy)

        return busy

    def taking_series(self):
        # logger.debug('Spectrometer %s: Taking series: %s', self.name,
        #     self._taking_series)
        return self._taking_series

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
        if self._live_update:
            self._live_update_evt.clear()
            self._live_update_paused.wait()
            while self.is_busy():
                time.sleep(0.1)

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
                    absorbance_wavelengths=self._absorbance_wavelengths,
                    wl_range_idx=self._wavelength_range_idx)

                self.set_dark(avg_spec)
            else:
                raise RuntimeError('Spectrometer is not in dark conditions, so '
                    'a dark reference spectrum could not be collected.')

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        if self._live_update:
            self._live_update_evt.set()

        return self.get_dark()

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
        if self._live_update:
            self._live_update_evt.clear()
            self._live_update_paused.wait()
            while self.is_busy():
                time.sleep(0.1)

        if not self.is_busy():
            if auto_dark:
                self._auto_dark(dark_time)

            self._check_light_conditions()

            self._collect_reference_spectrum_inner(averages, dark_correct, int_trigger)

        else:
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        if self._live_update:
            self._live_update_evt.set()

        return self.get_reference_spectrum()

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
        auto_dark=True, dark_time=60*60, drift_correct=True):
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

            self._check_light_conditions()

            logger.debug('Spectrometer %s: Collecting spectrum', self.name)

            if spec_type == 'abs':
                spectrum = self._collect_absorbance_spectrum_inner(dark_correct,
                    int_trigger, drift_correct)

            elif spec_type == 'trans':
                spectrum = self._collect_transmission_spectrum_inner(dark_correct,
                    int_trigger, drift_correct)
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
            absorbance_wavelengths=self._absorbance_wavelengths,
            wl_range_idx=self._wavelength_range_idx)

        if dark_correct:
            dark_spectrum = self.get_dark()

            spectrum.dark_correct(dark_spectrum)

        self._add_spectrum_to_history(spectrum)

        return spectrum

    def _collect_transmission_spectrum_inner(self, dark_correct, int_trigger,
        drift_correct):
        logger.debug('Spectrometer %s: Getting transmission spectrum', self.name)
        spectrum = self._collect_spectrum_inner(dark_correct, int_trigger)

        ref_spectrum = self.get_reference_spectrum()

        if drift_correct:
            spectrum.drift_correct(ref_spectrum, self._drift_window)

        spectrum.transmission_from_ref(ref_spectrum)

        self._add_spectrum_to_history(spectrum, spec_type='trans')

        return spectrum

    def _collect_absorbance_spectrum_inner(self, dark_correct, int_trigger,
        drift_correct):
        logger.debug('Spectrometer %s: Getting absorbance spectrum', self.name)

        spectrum = self._collect_transmission_spectrum_inner(dark_correct,
            int_trigger, drift_correct)

        self._add_spectrum_to_history(spectrum, spec_type='abs')

        if self._do_analog_out:
            for output, wav in self._analog_out_wavelengths.items():
                try:
                    wav = float(wav)
                except Exception:
                    wav = 0

                if wav > 0:
                    abs_val = spectrum.get_absorbance(wav)

                    output_val = (min(1., abs(abs_val/self._analog_out_au_max))
                        *self._analog_out_v_max)

                    if abs_val < 0:
                        output_val *= -1

                    self._set_analog_output(output, output_val)

        return spectrum

    def _live_update_spectra(self):
        start_time = 0

        while not self._live_update_stop.is_set():
            self._live_update_paused.set()
            self._live_update_evt.wait()
            if time.time() - start_time > self._live_update_period:
                self._live_update_paused.clear()
                try:
                    self.collect_spectrum(drift_correct=False)
                except Exception:
                    # traceback.print_exc()
                    pass
                start_time = time.time()

            else:
                time.sleep(0.1)

    def set_live_update(self, do_live_update, period=1):
        if do_live_update:
            self._live_update = True
            self._live_update_evt.set()

        else:
            old_live_update = copy.copy(self._live_update)

            self._live_update = False
            self._live_update_evt.clear()

            if old_live_update and not self._taking_series:
                while self.is_busy():
                    time.sleep(0.1)

        self._live_update_period = period

    def get_live_update(self):
        live_update_active = self._live_update and self._live_update_evt.is_set()
        return live_update_active, self._live_update_period

    def collect_spectra_series(self, num_spectra, spec_type='abs', return_q=None,
        delta_t_min=0, dark_correct=True, int_trigger=True, auto_dark=True,
        dark_time=60*60, take_ref=True, ref_avgs=1, drift_correct=True,
        wait_for_trig=False):
        if self._live_update and not self._taking_series:
            self._live_update_evt.clear()
            self._live_update_paused.wait()
            while self.is_busy():
                time.sleep(0.1)

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
                'take_ref' : take_ref, 'ref_avgs' : ref_avgs,
                'drift_correct': drift_correct, 'wait_for_trig': wait_for_trig})

            self._series_thread.daemon = True
            self._series_thread.start()

    def _collect_spectra_series(self, num_spectra, return_q=None, spec_type='abs',
        delta_t_min=0, dark_correct=True, int_trigger=True, auto_dark=True,
        dark_time=60*60, take_ref=True, ref_avgs=1, drift_correct=True,
        wait_for_trig=False):
        if self.is_busy():
            raise RuntimeError('A spectrum or series of spectrum is already being '
                'collected, cannot collect a new spectrum.')

        else:
            self._series_abort_event.clear()

            tot_spectrum = 0

            self._calculate_all_abs_single_wavelength()

            dt_delta_t = datetime.timedelta(seconds=delta_t_min)

            if self._series_abort_event.is_set():
                self._taking_series = False
                self.series_ready_event.clear()
                return

            if auto_dark:
                self._auto_dark(dark_time)

            ds = self.get_dark()

            if self._autosave_on:
                ds.save_spectrum('{}_dark.csv'.format(self._autosave_prefix),
                    self._autosave_dir, 'raw')

            self._check_light_conditions()

            # time.sleep(0.1)

            if take_ref:
                self._collect_reference_spectrum_inner(ref_avgs)

            ref = self.get_reference_spectrum()

            if self._autosave_on:
                ref.save_spectrum('{}_ref.csv'.format(self._autosave_prefix),
                    self._autosave_dir, 'raw')

            if spec_type == 'abs':
                abs_wavs = self.get_absorbance_wavelengths()

                absorbance = {wav : [] for wav in abs_wavs}

                abs_t = []

            if wait_for_trig:
                while not self._get_ext_trig_in():
                    pass

            self._taking_series = True

            while tot_spectrum < num_spectra:
                if self._series_abort_event.is_set():
                    break

                logger.debug('Spectrometer %s: Collecting series spectra %s',
                    self.name, tot_spectrum+1)

                if tot_spectrum == 0:
                    self.series_ready_event.set()

                if spec_type == 'abs':
                    # a = time.time()
                    spectrum = self._collect_absorbance_spectrum_inner(dark_correct,
                        int_trigger, drift_correct)
                    # logger.info('%s', time.time()-a)
                elif spec_type == 'trans':
                    spectrum = self._collect_transmission_spectrum_inner(dark_correct,
                        int_trigger, drift_correct)
                else:
                    spectrum = self._collect_spectrum_inner(dark_correct,
                        int_trigger)

                if self._autosave_on:
                    self._autosave_queue.append([spectrum, tot_spectrum, spec_type])

                    if tot_spectrum == 0:
                        initial_spec_ts = spectrum.get_timestamp()

                    dt = spectrum.get_timestamp()-initial_spec_ts
                    abs_t.append(dt.total_seconds())

                    if spec_type == 'abs':
                        for wav, abs_list in absorbance.items():
                            abs_list.append(spectrum.get_absorbance(wav))

                if return_q is not None:
                    logger.debug('Spectrometer %s: Returning series spectra %s',
                        self.name, tot_spectrum+1)

                    try:
                        return_q.put_nowait(spectrum)
                    except:
                        return_q.append(spectrum)

                tot_spectrum += 1

                ts = spectrum.get_timestamp()

                while datetime.datetime.now() -  ts < dt_delta_t:
                    if self._series_abort_event.is_set():
                        break

                    time.sleep(0.01)

            if self._autosave_on and spec_type == 'abs':
                out_file = os.path.join(self._autosave_dir,
                    '{}_absorbance.csv'.format(self._autosave_prefix))

                out_list = [abs_t] + [absorbance[wav] for wav in absorbance]
                out_data = np.column_stack(out_list)
                header = ('Absorbance\n#Averaging window: {} nm\n#Time_(s),'
                    .format(self.get_absorbance_window())
                                )
                for wav in absorbance:
                    header += 'Abs_{}_nm_(mAu),'.format(wav)
                header.rstrip(',')

                if out_data.size > 0:
                    np.savetxt(out_file, out_data, delimiter=',', header=header)

            self._taking_series = False
            self.series_ready_event.clear()

            if self._live_update:
                self._live_update_evt.set()

            logger.info('Spectrometer %s: Finished Collecting a series of '
                '%s spectra', self.name, num_spectra)

    def _series_autosave_thread(self):
        while True:
            if len(self._autosave_queue) > 0:
                spectrum, tot_spectrum, spec_type = self._autosave_queue.popleft()

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
            else:
                if self._stop_autosave_event.is_set():
                    break
                else:
                    time.sleep(0.1)

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

    def divide_spectra(self, spectrum1, spectrum2, spec_type='raw'):
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
        history['timestamps'].append((spectrum.get_timestamp().astimezone() -
            datetime.datetime(1970,1,1,
                tzinfo=datetime.timezone.utc)).total_seconds())

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
            now = (datetime.datetime.now(datetime.timezone.utc)- datetime.datetime(1970,1,1,
                    tzinfo=datetime.timezone.utc)).total_seconds()

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

        now = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime(1970,1,1,
            tzinfo=datetime.timezon.utc)).total_seconds()

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

    def get_full_history_ts(self, spec_type='abs'):
        logger.debug('Spectrometer %s: Getting full history of %s spectra',
            self.name, spec_type)

        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        return history

    def set_history_time(self, t):
        logger.debug('Spectrometer %s: Setting history time to %s', self.name, t)

        self._history_length = t

        self._prune_history(self._absorbance_history)
        self._prune_history(self._transmission_history)
        self._prune_history(self._history)

    def get_history_time(self):
        logger.debug('Spectrometer %s: Getting history length', self.name)
        return self._history_length

    def set_drift_window(self, window):
        logger.info(('Spectrometer %s: Setting drift correction window to '
                    '%s to %s nm.'), self.name, window[0], window[1])
        self._drift_window = window

    def get_drift_window(self):
        logger.debug('Spectrometer %s: Getting drift correction window', self.name)
        return self._drift_window

    def add_absorbance_wavelength(self, wavelength):
        logger.info('Spectrometer %s: Adding absorbance at %s nm', self.name,
            wavelength)
        if wavelength < self.wavelength[0] or wavelength > self.wavelength[-1]:
            raise RuntimeError('Wavelength is outside of measured range.')

        self._calculate_absorbance_range(wavelength)

    def _calculate_absorbance_range(self, wvl):
        wvl_start = wvl - self._absorbance_window/2
        wvl_end = wvl + self._absorbance_window/2

        if len(self._wavelength_range_idx) > 0 and self._wavelength_range_idx[0] is not None:
            start = self._wavelength_range_idx[0]
            end = self._wavelength_range_idx[1]
            wavelength = self.wavelength[start:end+1]
        else:
            wavelength = self.wavelength

        _, start_idx = utils.find_closest(wvl_start, wavelength)
        _, end_idx = utils.find_closest(wvl_end, wavelength)

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

        self._calculate_all_abs_single_wavelength()

    def _calculate_all_abs_single_wavelength(self):
        for wavelength in self._absorbance_wavelengths:
            self._calculate_absorbance_range(wavelength)

    def get_absorbance_window(self):
        logger.debug('Spectrometer %s: Getting absorbance window', self.name)
        return self._absorbance_window

    def get_analog_out_params(self):
        logger.debug('Spectrometer %s: Getting analog out params', self.name)
        ao_params = {
            'do_analog_out': self._do_analog_out,
            'analog_out_v_max': self._analog_out_v_max,
            'analog_out_au_max': self._analog_out_au_max,
            'analog_out_wavelengths': self._analog_out_wavelengths,
            }

        return ao_params

    def set_analog_out_params(self, **kwargs):
        logger.info(('Spectrometer %s: Setting analog output parameters: %s'),
            self.name, kwargs)
        if 'do_ao' in kwargs:
            self._do_analog_out = kwargs['do_ao']

        if 'ao_v_max' in kwargs:
            self._analog_out_v_max = kwargs['ao_v_max']

        if 'ao_au_max' in kwargs:
            self._analog_out_au_max = kwargs['ao_au_max']

        if 'ao_wav' in kwargs:
            self._analog_out_wavelengths = kwargs['ao_wav']

        if not self._do_analog_out:
            for output, wav in self._analog_out_wavelengths.items():
                self._set_analog_output(output, 0)
        else:
            for output, wav in self._analog_out_wavelengths.items():
                try:
                    float(wav)
                except Exception:
                    self._set_analog_output(output, 0)


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

    def set_wavelength_range(self, start, end):
        logger.info('Spectrometer %s: Setting wavelength range %s to %s',
            self.name, start, end)
        if start is not None and start >= self.wavelength[0]:
            self._wavelength_range[0] = start

            val, idx = utils.find_closest(start, self.wavelength)
            self._wavelength_range_idx[0] = idx

        elif start is None or start < self.wavelength[0]:
            self._wavelength_range[0] = None
            self._wavelength_range_idx[0] = 0

        if end is not None and end <= self.wavelength[-1]:
            self._wavelength_range[1] = end

            val, idx = utils.find_closest(end, self.wavelength)
            self._wavelength_range_idx[1] = idx

        elif end is None or end > self.wavelength[-1]:
            self._wavelength_range[1] = None
            self._wavelength_range_idx[1] = len(self.wavelength)

        self._reference_spectrum = None
        self._dark_spectrum = None
        self._calculate_all_abs_single_wavelength()

    def get_wavelength_range(self):
        logger.debug('Sepctrometer %s: Getting wavelength range', self.name)
        return self._wavelength_range

    def get_autosave_parameters(self):
        logger.debug('Spectrometer %s: Getting series autosave parameters',
            self.name)
        return (self._autosave_dir, self._autosave_prefix, self._autosave_raw,
            self._autosave_trans, self._autosave_abs)

    def abort_collection(self):
        logger.info('Spectrometer %s: Aborting collection', self.name)
        self._series_abort_event.set()
        self.abort_collection()

class StellarnetUVVis(Spectrometer):
    """
    Stellarnet black comet UV-Vis spectrometer
    """

    def __init__(self, name, device, shutter_pv_name='18ID:E1608:Bo1',
        trigger_pv_name='18ID:LJT4:2:Bo12', out1_pv_name='18ID:E1608:Ao1',
        out2_pv_name='18ID:E1608:Ao2', trigger_in_pv_name='18ID_E1608:Bi8',
        deut_lamp_ctrl_pv_name='18ID:E1608:Bo2',
        hal_lamp_ctrl_pv_name='18ID:E1608:Bo3',
        deut_lamp_status_pv_name='18ID:E1608:Bi4',
        hal_lamp_status_pv_name='18ID:E1608:Bi5'):

        Spectrometer.__init__(self, name, device)

        self._x_timing = 3
        self._temp_comp = None
        self._coeffs = None
        self._det_type = None
        self._model = None
        self._device_id = None

        self._ext_trig = False

        self.connected = False


        self.trigger_pv = epics.get_pv(trigger_pv_name)
        self.trigger_pv.get()

        self.analog_outs = {'out1': epics.get_pv(out1_pv_name),
            'out2': epics.get_pv(out2_pv_name),}

        self.analog_outs['out1'].get()
        self.analog_outs['out2'].get()

        self.trigger_in_pv = epics.get_pv(trigger_in_pv_name)
        self.trigger_in_pv.get()

        # Lightsource PVs
        self.shutter_pv = epics.get_pv(shutter_pv_name)
        self.uv_lamp_ctrl_pv = epics.get_pv(deut_lamp_ctrl_pv_name)
        self.vis_lamp_ctrl_pv = epics.get_pv(hal_lamp_ctrl_pv_name)
        self.uv_lamp_status_pv = epics.get_pv(deut_lamp_status_pv_name)
        self.vis_lamp_status_pv = epics.get_pv(hal_lamp_status_pv_name)

        self.shutter_pv.get()
        self.uv_lamp_ctrl_pv.get()
        self.vis_lamp_ctrl_pv.get()
        self.uv_lamp_status_pv.get()
        self.vis_lamp_status_pv.get()

        self.connect()

        if self.connected:
            self._get_config()

    def connect(self):
        if not self.connected:
            logger.info('Spectrometer %s: Connecting', self.name)

            if sn is not None:
                spec, wav = sn.array_get_spec(0)

                self.spectrometer = spec
                self.wav = wav

                self.wavelength = self.wav.reshape(self.wav.shape[0])
                self.set_wavelength_range(self.wavelength[0], self.wavelength[-1])

                self.connected = True
            else:
                self.connected = False

        if self._live_update and not self._taking_series:
            self._live_update_evt.set()

        return self.connected

    def disconnect(self):
        logger.info('Spectrometer %s: Disconnecting', self.name)

        if self.is_busy():
            self.abort_collection()
        self.spectrometer['device'].__del__()
        self._stop_autosave_event.set()
        self._live_update_stop.set()
        self._live_update_thread.join(5)

    def set_integration_time(self, int_time, update_dark=True):
        logger.info('Spectrometer %s: Setting integration time to %s s',
            self.name, int_time)

        if int_time != self._integration_time:
            self._set_config(int_time, self._scan_avg, self._smoothing,
                self._x_timing)

            if update_dark:
                self.collect_dark()

    def set_scan_avg(self, num_avgs, update_dark=True):
        logger.info('Spectrometer %s: Setting number of scans to average for '
            'each collected spectra to %s', self.name, num_avgs)

        if num_avgs != self._scan_avg:
            self._set_config(self._integration_time, num_avgs,
                self._smoothing, self._x_timing)

            if update_dark:
                self.collect_dark()

    def set_smoothing(self, smooth, update_dark=True):
        logger.info('Spectrometer %s: Setting smoothing to %s', self.name,
            smooth)

        if smooth != self._smoothing:
            self._set_config(self._integration_time, self._scan_avg, smooth,
                self._x_timing)

            if update_dark:
                self.collect_dark()

    def set_xtiming(self, x_timing, update_dark=True):
        logger.info('Spectrometer %s: Setting x timing to %s', self.name,
            x_timing)

        if x_timing != self._x_timing:
            self._set_config(self._integration_time, self._scan_avg,
                self._smoothing, x_timing)

            if update_dark:
                self.collect_dark()

    def get_xtiming(self):
        logger.debug('Spectrometer %s: X timing: %s', self.name, self._x_timing)

        return self._x_timing

    def set_lightsource_shutter(self, set_open):
        logger.debug('Spectrometer %s: Opening light source shutter: %s',
            self.name, set_open)

        if set_open:
            self.shutter_pv.put(1, wait=True)
        else:
            self.shutter_pv.put(0, wait=True)

        time.sleep(0.1)

    def get_lightsource_shutter(self):
        status = self.shutter_pv.get()

        logger.debug('Spectrometer %s: Shutter open: %s',
            self.name, status)

        return status

    def set_lightsource_uv_lamp(self, set_on):
        logger.debug('Spectrometer %s: Setting UV lamp: %s',
            self.name, set_on)
        uv_lamp_status = self.get_lightsource_uv_lamp()

        if (set_on and not uv_lamp_status) or (not set_on and uv_lamp_status):
            self.uv_lamp_ctrl_pv.put(1, wait=True)
            time.sleep(0.1)
            self.uv_lamp_ctrl_pv.put(0, wait=True)

    def get_lightsource_uv_lamp(self):
        status = self.uv_lamp_status_pv.get()
        #Light source is active low
        status = not status

        logger.debug('Spectrometer %s: UV lamp on: %s',
            self.name, status)

        return status

    def set_lightsource_vis_lamp(self, set_on):
        logger.debug('Spectrometer %s: Setting Vis lamp: %s',
            self.name, set_on)

        vis_lamp_status = self.get_lightsource_vis_lamp()

        if (set_on and not vis_lamp_status) or (not set_on and vis_lamp_status):
            self.vis_lamp_ctrl_pv.put(1, wait=True)
            time.sleep(0.1)
            self.vis_lamp_ctrl_pv.put(0, wait=True)

    def get_lightsource_vis_lamp(self):
        status = self.vis_lamp_status_pv.get()

        #Light source is active low
        status = not status

        logger.debug('Spectrometer %s: Vis lamp on: %s',
            self.name, status)

        return status

    def set_int_trigger(self, trigger):
        with self._spectrometer_lock:
            if trigger:
                self.trigger_pv.put(1, wait=True)
            else:
                self.trigger_pv.put(0, wait=True)

    def set_int_trigger_abort(self, trigger):
        if trigger:
            self.trigger_pv.put(1, wait=True)
        else:
            self.trigger_pv.put(0, wait=True)

    def get_int_trigger(self):
        return  self.trigger_pv.get()

    def _collect_spectrum(self, int_trigger):
        logger.debug('Spectrometer %s: Collecting spectrum', self.name)
        with self._spectrometer_lock:
            self._taking_data = True

            if self._ext_trig and int_trigger:
                trigger_ext = True
                self.set_external_trigger(False)
            else:
                trigger_ext = False

            if int_trigger:
                trigger_status = self.get_int_trigger()
                if not trigger_status:
                    self.set_int_trigger(True)

            spectrum = sn.array_spectrum(self.spectrometer, self.wav)

            if int_trigger and not trigger_status:
                    self.set_int_trigger(False)

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
        dark = not self.get_lightsource_shutter()

        if not dark and set_dark_conditions:
            self.set_lightsource_shutter(False)

            dark = True

        return dark

    def _check_light_conditions(self, set_light_conditions=True):
        """
        Checks whether the spectrometer is light

        Parameters
        ----------
        set_light_conditions: bool, optional
            If True (default) will attempt to set light conditions properly

        Returns
        -------
        is_light: bool
            Whether spectrometer is currently in a light condition
        """
        logger.debug('Spectrometer %s: Checking light conditions', self.name)
        light = self.get_lightsource_shutter()

        if not light and set_light_conditions:
            self.set_lightsource_shutter(True)

            light = True

        return light

    # function defination to set parameter
    def _set_config(self, int_time, num_avgs, smooth, xtiming):
        int_time = int(round(int_time*1000))
        self._integration_time = int_time/1000
        self._scan_avg = int(num_avgs)
        self._smoothing = int(smooth)
        self._x_timing = int(xtiming)

        with self._spectrometer_lock:
            self.spectrometer['device'].set_config(int_time=int_time,
                scans_to_avg=self._scan_avg, x_smooth=self._smoothing,
                x_timing=self._x_timing)

            self._collect_spectrum(True)

    def _get_config(self):
        with self._spectrometer_lock:
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
        with self._spectrometer_lock:
            self._ext_trig = trigger
            sn.ext_trig(self.spectrometer, trigger)

    def get_external_trigger(self):
        return self._ext_trig

    def _set_analog_output(self, output, val):
        """
        Sets the analog output value, used for integration with external
        equipment such as the the Wyatt MALS/DLS
        """
        dev = self.analog_outs[output]
        dev.put(val, wait=False)

    def _get_ext_trig_in(self):
        state = self.trigger_in_pv.get()
        return state

    def abort_collection(self):
        logger.info('Spectrometer %s: Aborting collection', self.name)
        self._series_abort_event.set()

        int_trig = self.get_int_trigger()

        if not int_trig:
            self.set_int_trigger_abort(True)
            time.sleep(1)
            self.set_int_trigger_abort(False)


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
            'get_full_hist_ts'  : self._get_full_history_ts,
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
            'set_external_trig' : self._set_external_trig,
            'get_external_trig' : self._get_external_trig,
            'abort_collection'  : self._abort_collection,
            'get_busy'          : self._get_busy,
            'get_spec_settings' : self._get_spec_settings,
            'get_ls_status'     : self._get_ls_status,
            'set_ls_shutter'    : self._set_lightsource_shutter,
            'get_ls_shutter'    : self._get_lightsource_shutter,
            'set_uv_lamp'       : self._set_lightsource_uv_lamp,
            'get_uv_lamp'       : self._get_lightsource_uv_lamp,
            'set_vis_lamp'      : self._set_lightsource_vis_lamp,
            'get_vis_lamp'      : self._get_lightsource_vis_lamp,
            'set_int_trig'      : self._set_internal_trigger,
            'set_wl_range'      : self._set_wavelength_range,
            'get_wl_range'      : self._get_wavelength_range,
            'set_drift_window'  : self._set_drift_window,
            'get_drift_window'  : self._get_drift_window,
            'set_ao_params'     : self._set_ao_params,
            'get_ao_params'     : self._get_ao_params,
            'set_live_update'   : self._set_live_update,
            'get_live_update'   : self._get_live_update,
        }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = {
            'StellarNet' : StellarnetUVVis,
            }

        self._series_q = {}

        self._monitor_threads = {}

    def _additional_new_comm(self, name):
        pass

    def _additional_connect_device(self, name, device_type, device, **kwargs):
        self._series_q[name] = deque()

    def _set_int_time(self, name, val, **kwargs):
        logger.debug("Setting device %s integration time to %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_integration_time(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s integraiton time set", name)

    def _set_scan_avg(self, name, val, **kwargs):
        logger.debug("Setting device %s scan averages to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_scan_avg(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s scan averages set", name)

    def _set_smoothing(self, name, val, **kwargs):
        logger.debug("Setting device %s smoothing to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_smoothing(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s smoothing set", name)

    def _set_xtiming(self, name, val, **kwargs):
        logger.debug("Setting device %s xtiming to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_xtiming(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s xtiming set", name)

    def _get_int_time(self, name, **kwargs):
        logger.debug("Getting device %s integration time", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_integration_time(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s integration time is %s s", name, val)

    def _get_scan_avg(self, name, **kwargs):
        logger.debug("Getting device %s scan averages", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_scan_avg(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s scan averages: %s", name, val)

    def _get_smoothing(self, name, **kwargs):
        logger.debug("Getting device %s smoothing", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_smoothing(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s smoothing: %s", name, val)

    def _get_xtiming(self, name, **kwargs):
        logger.debug("Getting device %s xtiming", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_xtiming(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s xtiming: %s", name, val)

    def _set_dark(self, name, val, **kwargs):
        logger.debug("Setting device %s dark to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_dark(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s dark set", name)

    def _get_dark(self, name, **kwargs):
        logger.debug("Getting device %s dark", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        try:
            val = device.get_dark(**kwargs)
        except RuntimeError:
            val = None

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s dark: %s", name, val)

    def _collect_dark(self, name, **kwargs):
        logger.debug("Collecting device %s dark", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        live_update, _ = device.get_live_update()

        if live_update:
            device.set_live_update(False)
            while device.is_busy():
                time.sleep(0.1)

        val = device.collect_dark(**kwargs)

        if live_update:
            device.set_live_update(True)

        self._return_value((name, cmd, val), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("Device %s dark: %s", name, val)

    def _set_ref(self, name, val, **kwargs):
        logger.debug("Setting device %s ref to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_reference_spectrum(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s ref set", name)

    def _get_ref(self, name, **kwargs):
        logger.debug("Getting device %s ref", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        try:
            val = device.get_reference_spectrum(**kwargs)
        except RuntimeError:
            val = None

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s ref: %s", name, val)

    def _collect_ref(self, name, **kwargs):
        logger.debug("Collecting device %s ref", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        live_update, _ = device.get_live_update()

        if live_update:
            device.set_live_update(False)
            while device.is_busy():
                time.sleep(0.1)

        val = device.collect_reference_spectrum(**kwargs)

        if live_update:
            device.set_live_update(True)

        self._return_value((name, cmd, val), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("Device %s ref: %s", name, val)

    def _collect_spec(self, name, **kwargs):
        logger.debug("Collecting device %s spectrum", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        live_update, _ = device.get_live_update()

        if live_update:
            device.set_live_update(False)
            while device.is_busy():
                time.sleep(0.1)

        val = device.collect_spectrum(**kwargs)

        if live_update:
            device.set_live_update(True)

        self._return_value((name, cmd, val), comm_name)
        self._return_value((name, cmd, val), 'status')

        logger.debug("Device %s spectrum: %s", name, val)

    def _collect_series(self, name, val, **kwargs):
        logger.debug("Collecting device %s spectra series of %s spectra", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        self._return_value((name, 'collect_series_start', val), 'status')

        device = self._connected_devices[name]
        series_q = self._series_q[name]
        series_q.clear()
        kwargs['return_q'] = series_q

        device.collect_spectra_series(val, **kwargs)

        monitor_thread = threading.Thread(target=self._monitor_series, args=(name,))
        monitor_thread.daemon = True
        monitor_thread.start()

        self._monitor_threads[name] = monitor_thread

        while not device.series_ready_event.is_set():
            time.sleep(0.01)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s series started", name)

    def _get_last_n_spectra(self, name, val, **kwargs):
        logger.debug("Getting device %s %s most recent spectra", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        hist = device.get_last_n_spectra(val, **kwargs)

        self._return_value((name, cmd, hist), comm_name)

        logger.debug("Device %s history returned", name)

    def _get_spectra_in_last_t(self, name, val, **kwargs):
        logger.debug("Getting device %s spectra in the last %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        hist = device.get_spectra_in_last_t(val, **kwargs)

        self._return_value((name, cmd, hist), comm_name)

        logger.debug("Device %s history returned", name)

    def _get_full_history(self, name, **kwargs):
        logger.debug("Getting device %s full spectra history", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_full_history(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s history returned", name)

    def _get_full_history_ts(self, name, **kwargs):
        logger.debug("Getting device %s full history", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_full_history_ts(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s history returned", name)

    def _set_history_time(self, name, val, **kwargs):
        logger.debug("Setting device %s history length to %s s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_history_time(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s history length set", name)

    def _get_history_time(self, name, **kwargs):
        logger.debug("Getting device %s history length", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_history_time(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s history time: %s s", name, val)

    def _add_absorbance_wavelength(self, name, val, **kwargs):
        logger.debug("Device %s adding absorbance wavelenght %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.add_absorbance_wavelength(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s absorbance wavelenght added", name)

    def _get_absorbance_wavelengths(self, name, **kwargs):
        logger.debug("Getting device %s absorbance wavelengths", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_absorbance_wavelengths(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s absorbance wavelengths: %s", name, val)

    def _remove_absorbance_wavelength(self, name, val, **kwargs):
        logger.debug("Device %s removing absorbance wavelenght %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.remove_absorbance_wavelength(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s absorbance wavelength removed", name)

    def _set_absorbance_window(self, name, val, **kwargs):
        logger.debug("Device %s setting absorbance window %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_absorbance_window(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s absorbance window set", name)

    def _get_absorbance_window(self, name, **kwargs):
        logger.debug("Getting device %s absorbance window", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_absorbance_window(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s absorbance window: %s nm", name, val)

    def _set_autosave_on(self, name, val, **kwargs):
        logger.debug("Device %s setting series autosave to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_autosave(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s autosave on set", name)

    def _get_autosave_on(self, name, **kwargs):
        logger.debug("Getting device %s autosave on", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_autosave(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s autosave on: %s", name, val)

    def _set_autosave_params(self, name, data_dir, prefix, **kwargs):
        logger.debug("Device %s setting series autosave parameters",
            name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_autosave_parameters(data_dir, prefix, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s autosave parameters set", name)

    def _get_autosave_params(self, name, **kwargs):
        logger.debug("Getting device %s autosave parameters", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_autosave_parameters(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s autosave parameters: %s", name, val)

    def _set_external_trig(self, name, val, **kwargs):
        logger.debug("Device %s setting external trigger %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_external_trigger(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s external trigger set", name)

    def _get_external_trig(self, name, **kwargs):
        logger.debug("Getting device %s external trigger", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_external_trigger(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s external trigger: %s", name, val)

    def _get_busy(self, name, **kwargs):
        logger.debug("Getting device %s busy", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.is_busy(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s busy: %s", name, val)

    def _abort_collection(self, name, **kwargs):
        logger.debug("Aborting device %s collection", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.abort_collection(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s aborted colelction", name)

    def _get_spec_settings(self, name, **kwargs):
        logger.debug('Getting device %s settings', name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        int_time = device.get_integration_time()
        scan_avg = device.get_scan_avg()
        smooth = device.get_smoothing()
        xtiming = device.get_xtiming()

        try:
            dark = device.get_dark()
        except RuntimeError:
            dark = None

        try:
            ref = device.get_reference_spectrum()
        except RuntimeError:
            ref = None

        abs_wavs = device.get_absorbance_wavelengths()
        abs_win = device.get_absorbance_window()
        hist_t = device.get_history_time(**kwargs)
        ls_shutter = device.get_lightsource_shutter()
        ls_uv_lamp = device.get_lightsource_uv_lamp()
        ls_vis_lamp = device.get_lightsource_vis_lamp()
        wl_range = device.get_wavelength_range()
        drift_win = device.get_drift_window()
        ao_params = device.get_analog_out_params()
        live_update = device.get_live_update()

        ret_vals = {
            'int_time'  : int_time,
            'scan_avg'  : scan_avg,
            'smooth'    : smooth,
            'xtiming'   : xtiming,
            'dark'      : dark,
            'ref'       : ref,
            'abs_wavs'  : abs_wavs,
            'abs_win'   : abs_win,
            'hist_t'    : hist_t,
            'ls_shutter': ls_shutter,
            'ls_uv_lamp': ls_uv_lamp,
            'ls_vis_lamp': ls_vis_lamp,
            'wl_range'  : wl_range,
            'drift_win' : drift_win,
            'ao_on'     : ao_params['do_analog_out'],
            'ao_v_max'  : ao_params['analog_out_v_max'],
            'ao_au_max' : ao_params['analog_out_au_max'],
            'ao_wavs'   : ao_params['analog_out_wavelengths'],
            'live_update': live_update,
        }

        self._return_value((name, cmd, ret_vals), comm_name)

        logger.debug('Got device %s settings: %s', name, ret_vals)

    def _get_ls_status(self, name, **kwargs):
        logger.debug('Getting device %s light source status', name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        ls_shutter = device.get_lightsource_shutter()
        ls_uv_lamp = device.get_lightsource_uv_lamp()
        ls_vis_lamp = device.get_lightsource_vis_lamp()

        ret_vals = {
            'ls_shutter': ls_shutter,
            'ls_uv_lamp': ls_uv_lamp,
            'ls_vis_lamp': ls_vis_lamp,
        }

        self._return_value((name, cmd, ret_vals), comm_name)

        logger.debug('Got device %s lightsource status: %s', name, ret_vals)

    def _set_lightsource_shutter(self, name, val, **kwargs):
        logger.debug("Device %s setting lightsource shutter %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_lightsource_shutter(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s lightsource shutter set", name)

    def _get_lightsource_shutter(self, name, **kwargs):
        logger.debug("Getting device %s lightsource shutter", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_lightsource_shutter(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s lightsource shutter: %s", name, val)

    def _set_lightsource_uv_lamp(self, name, val, **kwargs):
        logger.debug("Device %s setting lightsource uv lamp %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_lightsource_uv_lamp(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s lightsource uv lamp set", name)

    def _get_lightsource_uv_lamp(self, name, **kwargs):
        logger.debug("Getting device %s lightsource uv lamp", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_lightsource_uv_lamp(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s lightsource uv lamp: %s", name, val)

    def _set_lightsource_vis_lamp(self, name, val, **kwargs):
        logger.debug("Device %s setting lightsource vis lamp %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_lightsource_vis_lamp(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s lightsource vis lamp set", name)

    def _get_lightsource_vis_lamp(self, name, **kwargs):
        logger.debug("Getting device %s lightsource vis lamp", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_lightsource_vis_lamp(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s lightsource vis lamp: %s", name, val)

    def _set_internal_trigger(self, name, val, **kwargs):
        logger.debug("Device %s setting internal_trigger %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_int_trigger(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s internal_trigger set", name)

    def _set_wavelength_range(self, name, start, end, **kwargs):
        logger.debug("Device %s setting wavelength range %s to %s", name,
            start, end)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_wavelength_range(start, end, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s wavelength range set", name)

    def _get_wavelength_range(self, name, **kwargs):
        logger.debug("Getting device %s wavelength range", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_wavelength_range(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s wavelength range: %s to %s", name, val[0],
            val[1])

    def _set_drift_window(self, name, val, **kwargs):
        logger.debug("Device %s setting drift window %s nm", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_drift_window(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s drift window set", name)

    def _get_drift_window(self, name, **kwargs):
        logger.debug("Getting device %s drift window", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_drift_window(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s drift window: %s nm", name, val)

    def _set_ao_params(self, name, **kwargs):
        logger.debug("Device %s setting analog output params %s", name, kwargs)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_analog_out_params(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s analog output params set", name)

    def _get_ao_params(self, name, **kwargs):
        logger.debug("Getting device %s analog output params", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_analog_out_params(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s analog output params: %s", name, val)

    def _set_live_update(self, name, do_live_update, **kwargs):
        logger.debug("Device %s setting live_update params %s", name, kwargs)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_live_update(do_live_update, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Device %s live_update params set", name)

    def _get_live_update(self, name, **kwargs):
        logger.debug("Getting device %s live update params", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_live_update(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Device %s live_update params: %s", name, val)

    def _monitor_series(self, name):
        device = self._connected_devices[name]

        with self._queue_lock:
            series_q = self._series_q[name]

        while device.taking_series():
            if self._abort_event.is_set():
                break

            if len(series_q) > 0:
                spectrum = series_q.popleft()
                self._return_value((name, 'collect_series', spectrum), 'status')

            else:
                time.sleep(0.01)

        self._return_value((name, 'collect_series_end', True), 'status')

    def _additional_abort(self):
        for mon_thread in self._monitor_threads.values():
            mon_thread.join()

    def _cleanup_devices(self):
        for device in self._connected_devices.values():
            device.abort_collection()
            device.disconnect()

class UVPanel(utils.DevicePanel):

    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        try:
            biocon = wx.FindWindowByName('biocon')
        except Exception:
            biocon = None

        if biocon is not None:
            settings['device_data'] = settings['device_init'][0]

        if settings['inline_panel']:
            self.inline = True
        else:
            self.inline = False

        if settings['device_communication'] == 'remote':
            settings['remote'] = True

        self._dark_spectrum = None
        self._reference_spectrum = None
        self._current_spectrum = None

        self._history_length = 2*60*60

        self._history = {'spectra' : [], 'timestamps' : []}
        self._transmission_history = {'spectra' : [], 'timestamps' : []}
        self._absorbance_history = {'spectra' : [], 'timestamps' : []}

        self._series_running = False
        self._series_count = 0
        self._series_total = 0

        self._history_length = None
        self._current_int_time = None
        self._current_scan_avg = None
        self._current_smooth = None
        self._current_xtiming = None
        self._current_drift_win = None
        self._current_abs_wav = None
        self._current_abs_win = None
        self._current_wav_range = None
        self._current_ao_on = None
        self._current_ao_v_max = None
        self._current_ao_au_max = None
        self._current_ao_wav = {'out1': None, 'out2': None}

        self._series_exp_time = None
        self._series_scan_avg = None

        self._ls_shutter = None
        self._ls_vis_lamp = None
        self._ls_uv_lamp = None

        self.uvplot_frame = None
        self.uv_plot = None

        # self._live_update_evt = threading.Event()
        # self._restart_live_update

        super(UVPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _create_layout(self):
        """Creates the layout for the panel."""

        if not self.inline:
            status_parent = wx.StaticBox(self, label='Status:')
            self.status = wx.StaticText(status_parent, size=self._FromDIP((150, -1)),
                style=wx.ST_NO_AUTORESIZE)
            self.status.SetForegroundColour(wx.RED)
            fsize = self.GetFont().GetPointSize()
            font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
            self.status.SetFont(font)

            status_sizer = wx.StaticBoxSizer(status_parent, wx.HORIZONTAL)
            status_sizer.Add(wx.StaticText(status_parent, label='Status:'),
                flag=wx.ALL, border=self._FromDIP(5))
            status_sizer.Add(self.status, flag=wx.TOP|wx.BOTTOM|wx.LEFT,
                border=self._FromDIP(5))
            status_sizer.AddStretchSpacer(1)

            settings_parent = wx.StaticBox(self, label='Settings')

            self.live_update = wx.CheckBox(settings_parent, label='Continuously Update Spectrum')
            self.live_update.Bind(wx.EVT_CHECKBOX, self._on_live_update)

            self.int_time = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_te'))
            self.scan_avg = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('int_te'))
            self.smoothing = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('int_te'))
            self.xtiming = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('int_te'))
            self.spectrum_type = wx.Choice(settings_parent, choices=['Absorbance',
                'Transmission', 'Raw'])

            self.spectrum_type.SetStringSelection('Absorbance')

            self.xtiming_label = wx.StaticText(settings_parent, label='X Timing:')

            self.settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            self.settings_sizer.AddGrowableCol(1)

            self.settings_sizer.Add(wx.StaticText(settings_parent, label='Spectrum:'))
            self.settings_sizer.Add(self.spectrum_type, 1, flag=wx.EXPAND)
            self.settings_sizer.Add(wx.StaticText(settings_parent, label='Int. time (s):'))
            self.settings_sizer.Add(self.int_time, 1, flag=wx.EXPAND)
            self.settings_sizer.Add(wx.StaticText(settings_parent, label='Scans to avg.:'))
            self.settings_sizer.Add(self.scan_avg, 1, flag=wx.EXPAND)
            self.settings_sizer.Add(wx.StaticText(settings_parent, label='Smoothing:'))
            self.settings_sizer.Add(self.smoothing, 1, flag=wx.EXPAND)
            self.settings_sizer.Add(self.xtiming_label)
            self.settings_sizer.Add(self.xtiming, 1, flag=wx.EXPAND)


            self.dark_correct = wx.CheckBox(settings_parent, label='Dark correction')
            self.auto_dark = wx.CheckBox(settings_parent, label='Auto update dark')
            self.auto_dark_period = wx.TextCtrl(settings_parent,
                validator=utils.CharValidator('float'))
            self.dark_avgs = wx.TextCtrl(settings_parent,
                validator=utils.CharValidator('int'))
            self.ref_avgs = wx.TextCtrl(settings_parent,
                validator=utils.CharValidator('int'))
            self.drift_correct = wx.CheckBox(settings_parent, label='Drift correction')
            self.abs_wavs = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_list_pos_te'))
            self.abs_window = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_pos_te'))
            self.do_ao = wx.CheckBox(settings_parent, label='Analog Output')
            self.ao_au_max = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_pos_te'))
            self.ao_out1 = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_pos_te'))
            self.ao_out2 = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_pos_te'))
            self.history_time = utils.ValueEntry(self._on_settings_change,
                settings_parent, validator=utils.CharValidator('float_pos_te'))

            self.dark_correct.SetValue(True)
            self.auto_dark.SetValue(True)
            self.auto_dark_period.SetValue('{}'.format(60*60))
            self.dark_avgs.SetValue('3')
            self.drift_correct.SetValue(True)
            self.ref_avgs.SetValue('2')
            self.abs_wavs.SafeChangeValue('280, 260')
            self.abs_window.SafeChangeValue('1')
            self.do_ao.SetValue(True)
            self.ao_au_max.SafeChangeValue('10000')
            self.ao_out1.SafeChangeValue('280')
            self.ao_out2.SafeChangeValue('260')

            other_settings_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))

            other_settings_sizer.Add(self.dark_correct, (0,0), span=(1,2), flag=wx.EXPAND)
            other_settings_sizer.Add(self.auto_dark, (1,0), span=(1,2), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='Dark period (s):'),
                (2,0))
            other_settings_sizer.Add(self.auto_dark_period, (2,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='Dark averages:'),
                (3,0))
            other_settings_sizer.Add(self.dark_avgs, (3,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='Ref. averages:'),
                (4,0))
            other_settings_sizer.Add(self.ref_avgs, (4,1), flag=wx.EXPAND)
            other_settings_sizer.Add(self.drift_correct, (5,0), span=(1,2), flag=wx.EXPAND)

            other_settings_sizer.Add(wx.StaticText(settings_parent, label='Abs. wav.s (nm):'),
                (6,0))
            other_settings_sizer.Add(self.abs_wavs, (6,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='Abs. window (nm):'),
                (7,0))
            other_settings_sizer.Add(self.abs_window, (7,1), flag=wx.EXPAND)
            other_settings_sizer.Add(self.do_ao, (8,0), span=(1,2), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='AO mAu max:'),
                (9,0))
            other_settings_sizer.Add(self.ao_au_max, (9,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='AO 1 wav:'),
                (10,0))
            other_settings_sizer.Add(self.ao_out1, (10,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='AO 2 wav:'),
                (11,0))
            other_settings_sizer.Add(self.ao_out2, (11,1), flag=wx.EXPAND)
            other_settings_sizer.Add(wx.StaticText(settings_parent, label='History (s):'),
                (12,0))
            other_settings_sizer.Add(self.history_time, (12,1), flag=wx.EXPAND)

            other_settings_sizer.AddGrowableCol(1)

            settings_box_sizer = wx.StaticBoxSizer(settings_parent, wx.VERTICAL)
            settings_box_sizer.Add(self.live_update, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))
            settings_box_sizer.Add(self.settings_sizer,
                flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM, border=self._FromDIP(5))
            settings_box_sizer.Add(other_settings_sizer,
                flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM, border=self._FromDIP(5))


            single_spectrum_parent = wx.StaticBox(self, label='Collect Single Spectrum')

            self.collect_dark_btn = wx.Button(single_spectrum_parent,
                label='Collect Dark')
            self.collect_ref_btn = wx.Button(single_spectrum_parent,
                label='Collect Reference')
            self.collect_spectrum_btn = wx.Button(single_spectrum_parent,
                label='Collect Spectrum')

            self.collect_dark_btn.Bind(wx.EVT_BUTTON, self._on_collect_single)
            self.collect_ref_btn.Bind(wx.EVT_BUTTON, self._on_collect_single)
            self.collect_spectrum_btn.Bind(wx.EVT_BUTTON, self._on_collect_single)

            single_spectrum_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            single_spectrum_sizer.Add(self.collect_dark_btn, (0, 0))
            single_spectrum_sizer.Add(self.collect_ref_btn, (0, 1))
            single_spectrum_sizer.Add(self.collect_spectrum_btn, (1, 0), span=(1,2),
                flag=wx.ALIGN_CENTER_HORIZONTAL)

            single_spectrum_box_sizer = wx.StaticBoxSizer(single_spectrum_parent,
                wx.VERTICAL)
            single_spectrum_box_sizer.Add(single_spectrum_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))

            series_parent = wx.StaticBox(self, label='Collect Spectral Series')

            self.series_num = wx.TextCtrl(series_parent,
                validator=utils.CharValidator('int'))
            self.series_period = wx.TextCtrl(series_parent,
                validator=utils.CharValidator('float'))
            self.series_ref = wx.CheckBox(series_parent, label='Collect ref. at start')
            self.series_trig = wx.CheckBox(series_parent, label='Wait for trig. (not SAXS)')
            self.autosave_series = wx.CheckBox(series_parent, label='Autosave series')
            self.autosave_choice = wx.Choice(series_parent, choices=['Absorbance',
                'Transmission', 'Raw', 'A & T', 'A & T & R', 'A & R', 'T & R'])
            self.autosave_prefix = wx.TextCtrl(series_parent)
            self.autosave_dir = wx.TextCtrl(series_parent, style=wx.TE_READONLY,
                size=self._FromDIP((130,-1)))
            file_open = wx.ArtProvider.GetBitmap(wx.ART_FOLDER_OPEN, wx.ART_BUTTON,
                size=self._FromDIP((16,16)))
            self.change_dir_btn = wx.BitmapButton(series_parent, bitmap=file_open,
                size=self._FromDIP((30, -1)))
            self.collect_series_btn = wx.Button(series_parent,
                label='Collect Spectral Series')
            self.abort_series_btn = wx.Button(series_parent, label='Stop Series')

            self.series_num.SetValue('2')
            self.series_period.SetValue('0')
            self.series_ref.SetValue(True)
            self.series_trig.SetValue(False)
            self.autosave_series.SetValue(True)
            self.autosave_choice.SetStringSelection('Absorbance')
            self.autosave_dir.SetValue('.')
            self.autosave_prefix.SetValue('series')
            self.change_dir_btn.Bind(wx.EVT_BUTTON, self._on_change_dir)
            self.collect_series_btn.Bind(wx.EVT_BUTTON, self._on_collect_series)
            self.abort_series_btn.Bind(wx.EVT_BUTTON, self._on_abort_series)

            start_stop_sizer = wx.BoxSizer(wx.HORIZONTAL)
            start_stop_sizer.Add(self.collect_series_btn, flag=wx.RIGHT,
                border=self._FromDIP(5))
            start_stop_sizer.Add(self.abort_series_btn)

            series_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            series_sizer.Add(wx.StaticText(series_parent, label='# of spectra:'),
                (0,0))
            series_sizer.Add(self.series_num, (0,1), span=(1,2), flag=wx.EXPAND)
            series_sizer.Add(wx.StaticText(series_parent, label='Period (s):'),
                (1,0))
            series_sizer.Add(self.series_period, (1,1), span=(1,2), flag=wx.EXPAND)
            series_sizer.Add(self.series_ref, (2,0), span=(1,3))
            series_sizer.Add(self.series_trig, (3,0), span=(1,3))
            series_sizer.Add(self.autosave_series, (4,0), span=(1,3))
            series_sizer.Add(self.autosave_choice, (5,0), span=(1,3))
            series_sizer.Add(wx.StaticText(series_parent, label='Save prefix:'),
                (6,0))
            series_sizer.Add(self.autosave_prefix, (6,1), span=(1,2), flag=wx.EXPAND)
            series_sizer.Add(wx.StaticText(series_parent, label='Save dir.:'),
                (7,0))
            series_sizer.Add(self.autosave_dir, (7,1), flag=wx.EXPAND)
            series_sizer.Add(self.change_dir_btn, (7,2))
            series_sizer.Add(start_stop_sizer, (8,0), span=(1,3),
                flag=wx.ALIGN_CENTER_HORIZONTAL)

            series_sizer.AddGrowableCol(1)

            series_box_sizer = wx.StaticBoxSizer(series_parent,
                wx.VERTICAL)
            series_box_sizer.Add(series_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))

            ls_parent = wx.StaticBox(self, label='Light Source Control')

            self.ls_status = wx.StaticText(ls_parent, size=(150,-1),
                style=wx.ST_NO_AUTORESIZE)
            self.ls_open = wx.Button(ls_parent, label='Open Shutter')
            self.ls_close = wx.Button(ls_parent, label='Close Shutter')

            self.ls_uv_status = wx.StaticText(ls_parent, size=(150,-1),
                style=wx.ST_NO_AUTORESIZE)
            self.ls_uv_on = wx.Button(ls_parent, label='UV Lamp On')
            self.ls_uv_off = wx.Button(ls_parent, label='UV Lamp Off')
            self.ls_vis_status = wx.StaticText(ls_parent, size=(150,-1),
                style=wx.ST_NO_AUTORESIZE)
            self.ls_vis_on = wx.Button(ls_parent, label='Vis Lamp On')
            self.ls_vis_off = wx.Button(ls_parent, label='Vis Lamp Off')

            self.ls_open.Bind(wx.EVT_BUTTON, self._on_ls_shutter)
            self.ls_close.Bind(wx.EVT_BUTTON, self._on_ls_shutter)
            self.ls_uv_on.Bind(wx.EVT_BUTTON, self._on_ls_uv_lamp)
            self.ls_uv_off.Bind(wx.EVT_BUTTON, self._on_ls_uv_lamp)
            self.ls_vis_on.Bind(wx.EVT_BUTTON, self._on_ls_vis_lamp)
            self.ls_vis_off.Bind(wx.EVT_BUTTON, self._on_ls_vis_lamp)

            ls_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            ls_sizer.Add(wx.StaticText(ls_parent, label='Shutter:'))
            ls_sizer.Add(self.ls_status, flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_open, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_close, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(wx.StaticText(ls_parent, label='UV Lamp:'))
            ls_sizer.Add(self.ls_uv_status, flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_uv_on, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_uv_off, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(wx.StaticText(ls_parent, label='Vis Lamp:'))
            ls_sizer.Add(self.ls_vis_status, flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_vis_on, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_vis_off, flag=wx.ALIGN_CENTER_VERTICAL)

            ls_box_sizer = wx.StaticBoxSizer(ls_parent,
                wx.VERTICAL)
            ls_box_sizer.Add(ls_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))

            cs_sizer_1 = wx.BoxSizer(wx.VERTICAL)
            cs_sizer_1.Add(status_sizer, flag=wx.EXPAND|wx.BOTTOM,
                border=self._FromDIP(5))
            cs_sizer_1.Add(settings_box_sizer, flag=wx.EXPAND)

            cs_sizer_2 = wx.BoxSizer(wx.VERTICAL)
            cs_sizer_2.Add(single_spectrum_box_sizer,
                flag=wx.EXPAND|wx.BOTTOM, border=self._FromDIP(5))
            cs_sizer_2.Add(series_box_sizer,
                flag=wx.EXPAND|wx.BOTTOM, border=self._FromDIP(5))
            cs_sizer_2.Add(ls_box_sizer, flag=wx.EXPAND)

            control_sizer = wx.BoxSizer(wx.HORIZONTAL)
            control_sizer.Add(cs_sizer_1, proportion=1, flag=wx.ALL,
                border=self._FromDIP(5))
            control_sizer.Add(cs_sizer_2, proportion=1,
                flag=wx.TOP|wx.RIGHT|wx.BOTTOM, border=self._FromDIP(5))

            save_parent = wx.StaticBox(self, label='Save')

            self.save_dark = wx.Button(save_parent, label='Save Dark')
            self.save_dark.Bind(wx.EVT_BUTTON, self._on_save)
            self.save_ref = wx.Button(save_parent, label='Save Reference')
            self.save_ref.Bind(wx.EVT_BUTTON, self._on_save)
            self.save_current = wx.Button(save_parent, label='Save Latest Spectrum')
            self.save_current.Bind(wx.EVT_BUTTON, self._on_save)

            save_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
            save_btn_sizer.Add(self.save_current, border=self._FromDIP(5),
                flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL)
            save_btn_sizer.Add(self.save_ref, border=self._FromDIP(5),
                flag=wx.RIGHT|wx.TOP|wx.BOTTOM|wx.ALIGN_CENTER_VERTICAL)
            save_btn_sizer.Add(self.save_dark, border=self._FromDIP(5),
                flag=wx.RIGHT|wx.TOP|wx.BOTTOM|wx.ALIGN_CENTER_VERTICAL)

            save_sizer = wx.StaticBoxSizer(save_parent, wx.HORIZONTAL)
            save_sizer.Add(save_btn_sizer, flag=wx.EXPAND)
            save_sizer.AddStretchSpacer(1)


            plot_parent = wx.StaticBox(self, label='Plot')

            self.auto_update = wx.CheckBox(plot_parent, label='Auto update')
            self.auto_update.SetValue(False)
            self.auto_update.Bind(wx.EVT_CHECKBOX, self._on_autoupdate)

            self.update_period = utils.ValueEntry(self._on_plot_update_change,
                plot_parent, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((50,-1)))
            self.update_period.ChangeValue('0.5')
            self._on_plot_update_change(self.update_period, '0.5')

            plot_settings_sizer = wx.FlexGridSizer(cols=4, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            plot_settings_sizer.Add(self.auto_update, flag=wx.ALIGN_CENTER_VERTICAL)
            plot_settings_sizer.Add(wx.StaticText(plot_parent, label='Update period (s):'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            plot_settings_sizer.Add(self.update_period, flag=wx.ALIGN_CENTER_VERTICAL)

            self.uv_plot = UVPlot(self._get_plot_data_callback,
                self.settings['plot_refresh_t'], plot_parent)

            plot_sizer = wx.StaticBoxSizer(plot_parent, wx.VERTICAL)
            plot_sizer.Add(plot_settings_sizer, border=self._FromDIP(5),
                flag=wx.EXPAND|wx.BOTTOM)
            plot_sizer.Add(self.uv_plot, proportion=1, flag=wx.EXPAND)

            right_sizer = wx.BoxSizer(wx.VERTICAL)
            right_sizer.Add(save_sizer, border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
            right_sizer.Add(plot_sizer, proportion=1, border=self._FromDIP(5),
                flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM)

            top_sizer = wx.BoxSizer(wx.HORIZONTAL)
            top_sizer.Add(control_sizer, flag=wx.EXPAND)
            top_sizer.Add(right_sizer, proportion=1, flag=wx.EXPAND)

        else:
            status_parent = wx.StaticBox(self, label='Status:')
            self.status = wx.StaticText(status_parent, size=self._FromDIP((225, -1)),
                style=wx.ST_NO_AUTORESIZE)
            self.status.SetForegroundColour(wx.RED)
            fsize = self.GetFont().GetPointSize()
            font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
            self.status.SetFont(font)

            self.show_uv_plot = wx.Button(status_parent, label='Show Plot')
            self.show_uv_plot.Bind(wx.EVT_BUTTON, self._on_show_uv_plot)

            status_sizer = wx.StaticBoxSizer(status_parent, wx.HORIZONTAL)
            status_sizer.Add(wx.StaticText(status_parent, label='Status:'),
                flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=self._FromDIP(5))
            status_sizer.Add(self.status, border=self._FromDIP(5),
                flag=wx.TOP|wx.BOTTOM|wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
            status_sizer.Add(self.show_uv_plot, border=self._FromDIP(5),
                flag=wx.TOP|wx.BOTTOM|wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
            status_sizer.AddStretchSpacer(1)

            settings_parent = wx.StaticBox(self, label='Settings')

            self.int_time =wx.TextCtrl(settings_parent,
                validator=utils.CharValidator('float_te'))
            self.collect_uv = wx.CheckBox(settings_parent, label='Collect UV')
            self.collect_uv.SetValue(True)

            self.settings_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))

            self.settings_sizer.Add(wx.StaticText(settings_parent,
                label='Max int. time (s):'), (0,0), flag=wx.ALIGN_CENTER_VERTICAL)
            self.settings_sizer.Add(self.int_time, (0,1),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            self.settings_sizer.Add(self.collect_uv, (1,0), span=(1,2),
                flag=wx.ALIGN_CENTER_VERTICAL)


            self.settings_sizer.AddGrowableCol(1)

            settings_box_sizer = wx.StaticBoxSizer(settings_parent, wx.VERTICAL)
            settings_box_sizer.Add(self.settings_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))


            adv_pane = wx.CollapsiblePane(self, label="Advanced",
                style=wx.CP_NO_TLW_RESIZE)
            adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
            adv_win = adv_pane.GetPane()


            adv_settings_parent = wx.StaticBox(adv_win, label='Settings')

            self.auto_dark = wx.CheckBox(adv_settings_parent, label='Auto update dark')
            self.auto_dark_period = wx.TextCtrl(adv_settings_parent,
                validator=utils.CharValidator('float'))
            self.dark_avgs = wx.TextCtrl(adv_settings_parent,
                validator=utils.CharValidator('int'))
            self.ref_avgs = wx.TextCtrl(adv_settings_parent,
                validator=utils.CharValidator('int'))
            self.history_time = utils.ValueEntry(self._on_settings_change,
                adv_settings_parent, validator=utils.CharValidator('float_te'))

            adv_settings_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))

            adv_settings_sizer.Add(self.auto_dark, (0,0), span=(1,2),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(wx.StaticText(adv_settings_parent,
                label='Dark period (s):'), (1,0), flag=wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(self.auto_dark_period, (1,1),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(wx.StaticText(adv_settings_parent,
                label='Dark averages:'), (2,0), flag=wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(self.dark_avgs, (2,1),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(wx.StaticText(adv_settings_parent,
                label='Ref. averages:'), (3,0), flag=wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(self.ref_avgs, (3,1),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(wx.StaticText(adv_settings_parent,
                label='History (s):'), (4,0), flag=wx.ALIGN_CENTER_VERTICAL)
            adv_settings_sizer.Add(self.history_time, (4,1),
                flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)

            adv_settings_sizer.AddGrowableCol(1)

            adv_settings_box_sizer = wx.StaticBoxSizer(adv_settings_parent,
                wx.VERTICAL)
            adv_settings_box_sizer.Add(adv_settings_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))


            single_spectrum_parent = wx.StaticBox(adv_win,
                label='Collect Single Spectrum')

            self.collect_dark_btn = wx.Button(single_spectrum_parent,
                label='Collect Dark')
            self.collect_ref_btn = wx.Button(single_spectrum_parent,
                label='Collect Reference')

            self.collect_dark_btn.Bind(wx.EVT_BUTTON, self._on_collect_single)
            self.collect_ref_btn.Bind(wx.EVT_BUTTON, self._on_collect_single)

            single_spectrum_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            single_spectrum_sizer.Add(self.collect_dark_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)
            single_spectrum_sizer.Add(self.collect_ref_btn,
                flag=wx.ALIGN_CENTER_VERTICAL)

            single_spectrum_box_sizer = wx.StaticBoxSizer(single_spectrum_parent,
                wx.VERTICAL)
            single_spectrum_box_sizer.Add(single_spectrum_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))


            ls_parent = wx.StaticBox(adv_win, label='Light Source Control')

            self.ls_status = wx.StaticText(ls_parent, size=(150,-1),
                style=wx.ST_NO_AUTORESIZE)
            self.ls_open = wx.Button(ls_parent, label='Open Shutter')
            self.ls_close = wx.Button(ls_parent, label='Close Shutter')

            self.ls_open.Bind(wx.EVT_BUTTON, self._on_ls_shutter)
            self.ls_close.Bind(wx.EVT_BUTTON, self._on_ls_shutter)

            ls_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            ls_sizer.Add(wx.StaticText(ls_parent, label='Status:'))
            ls_sizer.Add(self.ls_status, flag=wx.EXPAND|wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_open, flag=wx.ALIGN_CENTER_VERTICAL)
            ls_sizer.Add(self.ls_close, flag=wx.ALIGN_CENTER_VERTICAL)

            ls_box_sizer = wx.StaticBoxSizer(ls_parent,
                wx.VERTICAL)
            ls_box_sizer.Add(ls_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))


            adv_sizer = wx.BoxSizer(wx.VERTICAL)
            adv_sizer.Add(adv_settings_box_sizer, 1, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))
            adv_sizer.Add(single_spectrum_box_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))
            adv_sizer.Add(ls_box_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))

            adv_win.SetSizer(adv_sizer)


            top_sizer = wx.BoxSizer(wx.VERTICAL)
            top_sizer.Add(status_sizer, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
                border=self._FromDIP(5))
            top_sizer.Add(settings_box_sizer, flag=wx.EXPAND|wx.ALL,
                border=self._FromDIP(5))
            top_sizer.Add(adv_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
                border=self._FromDIP(5))


        self.SetSizer(top_sizer)

    def on_collapse(self, event):
        self.Layout()
        self.Fit()

        self.parent.Layout()
        self.parent.Fit()

        try:
            wx.FindWindowByName('biocon').Layout()
            wx.FindWindowByName('biocon').Fit()
        except Exception:
            pass

    def _init_device(self, settings):
        """
        Initializes the device parameters if any were provided. If enough are
        provided the device is automatically connected.
        """
        self._init_controls()

        device_data = settings['device_data']
        args = copy.copy(device_data['args'])
        kwargs = device_data['kwargs']

        args.insert(0, self.name)

        connect_cmd = ['connect', args, kwargs]

        self._send_cmd(connect_cmd, True)

        # Need some kind of delay or I get a USB error message from the stellarnet driver?

        if self.inline:
            cmd = ['set_hist_time', [self.name, float(self._history_length)], {}]
            self._send_cmd(cmd, get_response=False)

        is_busy = self._get_busy()

        self._get_full_history()

        if not is_busy:
            self._set_abs_params()
            self._set_ao_params()

        cmd = ['get_spec_settings', [self.name,], {}]
        ret = self._send_cmd(cmd, True)
        self._set_status('get_spec_settings', ret)

        if not is_busy:
            self._set_wavelength_range()

        if not is_busy:
           self._init_dark_and_ref()

        self._set_status_commands()

        if args[1] != 'StellarNet' and not self.inline:
            self.settings_sizer.Hide(self.xtiming_label)
            self.setitngs_sizer.Hide(self.xtiming)
            self.parent.Layout()
            self.parent.Refresh()

    def _init_dark_and_ref(self):
        dark_cmd = ['get_dark', [self.name,], {}]

        dark = self._send_cmd(dark_cmd, True)

        if dark is None:
            self._collect_spectrum('dark')

        else:
            self._dark_spectrum = dark

        ref_cmd = ['get_ref', [self.name,], {}]

        ref = self._send_cmd(ref_cmd, True)

        if ref is None:
            self._collect_spectrum('ref')
        else:
            self._reference_spectrum = ref

    def _init_controls(self):
        if self.inline:
            self.auto_dark.SetValue(self.settings['auto_dark'])
            self.auto_dark_period.SetValue('{}'.format(self.settings['auto_dark_t']))
            self.dark_avgs.SetValue('{}'.format(self.settings['dark_avgs']))
            self.ref_avgs.SetValue('{}'.format(self.settings['dark_avgs']))
            self.int_time.SetValue('{}'.format(self.settings['max_int_t']))

            self._history_length = self.settings['history_t']
            self.history_time.SafeChangeValue('{}'.format(self.settings['history_t']))

        else:
            try:
                self.auto_dark.SetValue(self.settings['auto_dark'])
            except Exception:
                pass
            try:
                self.auto_dark_period.SetValue('{}'.format(self.settings['auto_dark_t']))
            except Exception:
                pass
            try:
                self.dark_avgs.SetValue('{}'.format(self.settings['dark_avgs']))
            except Exception:
                pass
            try:
                self.ref_avgs.SetValue('{}'.format(self.settings['dark_avgs']))
            except Exception:
                pass
            try:
                self.int_time.SafeChangeValue('{}'.format(self.settings['max_int_t']))
            except Exception:
                pass
            try:
                self._history_length = self.settings['history_t']
                self.history_time.SafeChangeValue('{}'.format(self.settings['history_t']))
            except Exception:
                pass
            try:
                self.dark_correct.SetValue(self.settings['dark_correct'])
            except Exception:
                pass
            try:
                self.drift_correct.SetValue(self.settings['drift_correct'])
            except Exception:
                pass
            try:
                self.abs_wavs.SafeChangeValue(', '.join(map(str, self.settings['abs_wav'])))
            except Exception:
                pass
            try:
                self.abs_window.SafeChangeValue('{}'.format(self.settings['abs_window']))
            except Exception:
                pass


    def _on_settings_change(self, obj, val):
        if obj == self.int_time:
            cmd = ['set_int_time', [self.name, float(val)], {}]

        elif obj == self.scan_avg:
            cmd = ['set_scan_avg', [self.name, int(val)], {}]

        elif obj == self.smoothing:
            cmd = ['set_smoothing', [self.name, int(val)], {}]

        elif obj == self.xtiming:
            cmd = ['set_xtiming', [self.name, int(val)], {}]

        elif obj == self.history_time:
            cmd = ['set_hist_time', [self.name, float(val)], {}]

        elif (obj == self.ao_au_max or obj == self.ao_out1 or obj == self.ao_out2):
            self._set_ao_params()
            cmd = None
        elif (obj == self.abs_wavs or obj == self.abs_window):
            self._set_abs_params()
            cmd = None
        else:
            cmd = None

        if cmd is not None:
            self._send_cmd(cmd, get_response=False)

    def _on_autoupdate(self, evt):
        status = self.auto_update.GetValue()

        if status:
            self._set_live_update(status)

            interval = self.update_period.GetValue()
            try:
                interval = float(interval)
            except Exception:
                interval = 1

            get_last_cmd = ['get_last_n', [self.name, 1], {}]
            self._update_status_cmd(get_last_cmd, interval)

        else:
            get_last_cmd = ['get_last_n', [self.name, 1], {}]
            self._update_status_cmd(get_last_cmd, 1e20)

    def _on_live_update(self, evt):
        status = self.live_update.GetValue()
        self._set_live_update(status)

        if not status:
            self.auto_update.SetValue(False)

    def _set_live_update(self, status):
        cmd = ['set_live_update', [self.name, status], {}]
        self._send_cmd(cmd, get_response=False)

    def _on_plot_update_change(self, obj, val):
        self._plot_update_period = float(val)

    def _set_wavelength_range(self):
        if self.inline:
            wav_start = self.settings['wavelength_range'][0]
            wav_end = self.settings['wavelength_range'][1]
        else:
            try:
                wav_start = self.settings['wavelength_range'][0]
                wav_end = self.settings['wavelength_range'][1]
            except Exception:
                wav_start = None

        update = False

        if wav_start is not None:
            if self._current_wav_range is not None:
                if ((self._current_wav_range[0] is None
                    and wav_start is not None) or
                    (self._current_wav_range[0] is not None
                    and wav_start is None) or
                    (self._current_wav_range[0] != wav_start)
                    or (self._current_wav_range[1] is None
                    and wav_end is not None) or
                    (self._current_wav_range[1] is not None
                    and wav_end is None) or
                    (self._current_wav_range[1] != wav_end)):
                    update = True

            else:
                update = True

        if update:
            cmd = ['set_wl_range', [self.name, wav_start, wav_end], {}]
            self._send_cmd(cmd, get_response=False)
            self._current_wav_range = [wav_start, wav_end]

    def _on_collect_single(self, evt):
        obj = evt.GetEventObject()

        # if not self.inline:
        #     self.auto_update.SetValue(False)
        #     # self._live_update_evt.clear()

        if obj == self.collect_dark_btn:
            self._collect_spectrum('dark')
        elif obj == self.collect_ref_btn:
            self._collect_spectrum('ref')
        elif obj == self.collect_spectrum_btn:
            self._collect_spectrum()

    def _on_ls_shutter(self, evt):
        obj = evt.GetEventObject()

        if obj == self.ls_open:
            shutter = True
        elif obj == self.ls_close:
            shutter = False

        self._open_ls_shutter(shutter)

    def _open_ls_shutter(self, shutter_open):
        ls_cmd = ['set_ls_shutter', [self.name, shutter_open], {}]
        self._send_cmd(ls_cmd, get_response=False)
        time.sleep(0.1)
        ls_status_cmd = ['get_ls_shutter', [self.name,], {}]
        resp = self._send_cmd(ls_status_cmd, True)

        self._ls_shutter = resp

        if resp:
            ls_status = 'Open'
        else:
            ls_status = 'Closed'

        self.ls_status.SetLabel(ls_status)

    def _on_ls_uv_lamp(self, evt):
        obj = evt.GetEventObject()

        if obj == self.ls_uv_on:
            lamp_state = True
        elif obj == self.ls_uv_off:
            lamp_state = False

        self._ls_uv_lamp_power(lamp_state)

    def _ls_uv_lamp_power(self, lamp_state):
        ls_uv_cmd = ['set_uv_lamp', [self.name, lamp_state], {}]
        self._send_cmd(ls_uv_cmd, get_response=True)
        time.sleep(1)
        ls_status_cmd = ['get_uv_lamp', [self.name,], {}]
        resp = self._send_cmd(ls_status_cmd, True)

        self._ls_uv_lamp = resp

        if resp:
            ls_uv_status = 'On'
        else:
            ls_uv_status = 'Off'

        self.ls_uv_status.SetLabel(ls_uv_status)

    def _on_ls_vis_lamp(self, evt):
        obj = evt.GetEventObject()

        if obj == self.ls_vis_on:
            lamp_state = True
        elif obj == self.ls_vis_off:
            lamp_state = False

        self._ls_vis_lamp_power(lamp_state)

    def _ls_vis_lamp_power(self, lamp_state):
        ls_uv_cmd = ['set_vis_lamp', [self.name, lamp_state], {}]
        self._send_cmd(ls_uv_cmd, get_response=True)
        time.sleep(1)
        ls_status_cmd = ['get_vis_lamp', [self.name,], {}]
        resp = self._send_cmd(ls_status_cmd, True)

        self._ls_vis_lamp = resp

        if resp:
            ls_vis_status = 'On'
        else:
            ls_vis_status = 'Off'

        self.ls_vis_status.SetLabel(ls_vis_status)

    def _collect_spectrum(self, stype='normal'):
        is_busy = self._get_busy()

        if not is_busy:
            if self.inline:
                dark_correct = self.settings['dark_correct']
                drift_correct = self.settings['drift_correct']
            else:
                dark_correct = self.dark_correct.GetValue()
                drift_correct = self.drift_correct.GetValue()

            self._set_wavelength_range()
            self._set_abs_params()

            if drift_correct:
                self._set_drift_params()
            self._set_ao_params()

            auto_dark = self.auto_dark.GetValue()
            dark_time = float(self.auto_dark_period.GetValue())

            if stype == 'normal':
                if self.inline:
                    spec_type = self.settings['spectrum_type']
                else:
                    spec_type = self.spectrum_type.GetStringSelection()

                if spec_type == 'Absorbance':
                    spec_type = 'abs'
                elif spec_type == 'Transmission':
                    spec_type == 'trans'
                else:
                    spec_type = 'raw'

                kwargs = {
                    'spec_type'     : spec_type,
                    'dark_correct'  : dark_correct,
                    'auto_dark'     : auto_dark,
                    'dark_time'     : dark_time,
                    'drift_correct' : drift_correct,
                }

                cmd = ['collect_spec', [self.name,], kwargs]

            elif stype == 'ref':
                avgs = int(self.ref_avgs.GetValue())

                kwargs = {
                    'averages'      : avgs,
                    'dark_correct'  : dark_correct,
                    'auto_dark'     : auto_dark,
                    'dark_time'     : dark_time,
                }

                cmd = ['collect_ref', [self.name], kwargs]

            elif stype == 'dark':
                avgs = int(self.dark_avgs.GetValue())

                cmd = ['collect_dark', [self.name,], {'averages': avgs}]

            else:
                cmd = None

            if cmd is not None:
                self._send_cmd(cmd, get_response=False)

        else:
            wx.CallAfter(self._show_busy_msg)

    def _on_collect_series(self, evt):
        # self._live_update_evt.clear()

        num_spectra = int(self.series_num.GetValue())
        period = float(self.series_period.GetValue())

        self._collect_series(num_spectra, period, None, None, None)

    def _collect_series(self, num_spectra, int_time, scan_avgs, exp_period,
        exp_time):
        is_busy = self._get_busy()

        if not is_busy:
            if self.inline:
                self._set_exposure_settings(int_time, scan_avgs)

                spec_type = self.settings['spectrum_type']
                dark_correct = self.settings['dark_correct']
                take_ref = self.settings['series_ref_at_start']
                drift_correct = self.settings['drift_correct']

                int_trigger = False
                wait_for_trig = False

                uv_time = max(int_time*self.settings['int_t_scale'], 0.05)*scan_avgs

                # delta_t_min = (exp_time-uv_time)*1.05
                delta_t_min = (exp_period-0.5-uv_time)*1.05

                if delta_t_min < 0.01:
                    delta_t_min = 0

            else:
                self._set_autosave_parameters(None, None)

                spec_type = self.spectrum_type.GetStringSelection()
                dark_correct = self.dark_correct.GetValue()
                take_ref = self.series_ref.GetValue()
                drift_correct = self.drift_correct.GetValue()

                delta_t_min = int_time

                int_trigger = True
                wait_for_trig = self.series_trig.GetValue()

            self._set_wavelength_range()
            self._set_abs_params()

            if drift_correct:
                self._set_drift_params()
            self._set_ao_params()

            auto_dark = self.auto_dark.GetValue()
            dark_time = float(self.auto_dark_period.GetValue())
            ref_avgs = int(self.ref_avgs.GetValue())

            if spec_type == 'Absorbance':
                spec_type = 'abs'
            elif spec_type == 'Transmission':
                spec_type == 'trans'
            else:
                spec_type = 'raw'

            kwargs = {
                'spec_type'     : spec_type,
                'delta_t_min'   : delta_t_min,
                'dark_correct'  : dark_correct,
                'int_trigger'   : int_trigger,
                'auto_dark'     : auto_dark,
                'dark_time'     : dark_time,
                'take_ref'      : take_ref,
                'ref_avgs'      : ref_avgs,
                'drift_correct' : drift_correct,
                'wait_for_trig' : wait_for_trig,
            }

            cmd = ['collect_series', [self.name, num_spectra], kwargs]

            self._send_cmd(cmd, get_response=False)

        else:
            wx.CallAfter(self._show_busy_msg)

        return not is_busy

    def _on_abort_series(self, evt):
        self._abort_series()

    def _abort_series(self):
        cmd = ['abort_collection', [self.name,], {}]
        self._send_cmd(cmd, get_response=False)

    def _get_busy(self):
        live_update_cmd = ['get_live_update', [self.name,], {}]
        live_update = self._send_cmd(live_update_cmd, True)

        if live_update is not None and live_update[0]:
            is_busy = False
        else:
            busy_cmd = ['get_busy', [self.name,], {}]
            is_busy = self._send_cmd(busy_cmd, True)

        return is_busy

    def _set_exposure_settings(self, exp_time, scan_avgs):
        update_dark = False

        self._series_exp_time = exp_time
        self._series_scan_avg = scan_avgs

        if exp_time != self._current_int_time:
            int_t_cmd = ['set_int_time', [self.name, exp_time],
                {'update_dark': False}]
            self._send_cmd(int_t_cmd, get_response=False)

            update_dark = True

        if scan_avgs != self._current_scan_avg:
            scan_avg_cmd = ['set_scan_avg', [self.name, scan_avgs],
                {'update_dark': False}]
            self._send_cmd(scan_avg_cmd, get_response=False)

            update_dark = True

        if self._current_smooth != self.settings['smoothing']:
            smoothing_cmd = ['set_smoothing', [self.name,
                self.settings['smoothing']], {'update_dark': False}]
            self._send_cmd(smoothing_cmd, get_response=False)

            update_dark = True

        if self._current_xtiming != self.settings['xtiming']:
            xtiming_cmd = ['set_xtiming', [self.name,
                self.settings['xtiming']], {'update_dark': False}]
            self._send_cmd(xtiming_cmd, get_response=False)

            update_dark = True

        if update_dark:
            self._collect_spectrum('dark')

    def _set_abs_params(self):
        if self.inline:
            abs_wav_list = self.settings['abs_wav']
            abs_window = self.settings['abs_window']

        else:
            wavs_str = self.abs_wavs.GetValue()
            abs_window = self.abs_window.GetValue()

            try:
                abs_window = float(abs_window)
            except Exception:
                abs_window = None

            abs_wav_split = wavs_str.split(',')

            abs_wav_list = []

            for wav in abs_wav_split:
                try:
                    abs_wav_list.append(float(wav))
                except Exception:
                    pass

        update = False

        for wav in abs_wav_list:
            if self._current_abs_wav is None or wav not in self._current_abs_wav:
                cmd = ['add_abs_wav', [self.name, wav], {}]
                self._send_cmd(cmd, get_response=False)
                update = True

        if self._current_abs_wav is not None:
            for wav in self._current_abs_wav:
                if wav not in abs_wav_list:
                    cmd = ['remove_abs_wav', [self.name, wav], {}]
                    self._send_cmd(cmd, get_response=False)
                    update = True

        if update:
            self._current_abs_wav = abs_wav_list

        if self._current_abs_win != abs_window:
            cmd = ['set_abs_window', [self.name, abs_window], {}]
            self._send_cmd(cmd, get_response=False)
            self._current_abs_win = abs_window

    def _set_drift_params(self):
        if self.inline:
            drift_window = self.settings['drift_window']
        else:
            try:
                drift_window = self.settings['drift_window']
            except Exception:
                drift_window = [-1, -1]

        if self._current_drift_win != drift_window:
            cmd = ['set_drift_window', [self.name, drift_window],{}]
            self._send_cmd(cmd, get_response=False)

    def _set_ao_params(self):
        if self.inline:
            do_ao = self.settings['do_ao']
            ao_v_max = self.settings['analog_out_v_max']
            ao_au_max = self.settings['analog_out_au_max']
            ao_wav = self.settings['analog_out_wav']
            params = {'do_ao': do_ao, 'ao_v_max': ao_v_max,
                'ao_au_max' : ao_au_max, 'ao_wav': ao_wav}
        else:
            do_ao = self.do_ao.GetValue()
            params = {'do_ao': do_ao}

            try:
                ao_au_max = float(self.ao_au_max.GetValue())
                params['ao_au_max'] = ao_au_max
            except Exception:
                ao_au_max = 0

            ao_wav = {}

            try:
                ao_out1 = float(self.ao_out1.GetValue())
                ao_wav['out1'] = ao_out1
            except Exception:
                ao_wav['out1'] = None

            try:
                ao_out2 = float(self.ao_out2.GetValue())
                ao_wav['out2'] = ao_out2
            except Exception:
                 ao_wav['out2'] = None

            params['ao_wav'] = ao_wav

            try:
                ao_v_max = self.settings['analog_out_v_max']
                params['ao_v_max'] = ao_v_max
            except Exception:
                ao_v_max = 0

        if (do_ao != self._current_ao_on or ao_v_max != self._current_ao_v_max
            or ao_au_max != self._current_ao_au_max
            or ao_wav != self._current_ao_wav):
                cmd = ['set_ao_params', [self.name,], params]
                self._send_cmd(cmd, get_response=False)

        if do_ao != self._current_ao_on:
            self._current_ao_on = do_ao

        if ao_v_max != self._current_ao_v_max:
            self._current_ao_v_max = ao_v_max

        if ao_au_max != self._current_ao_au_max:
            self._current_ao_au_max = ao_au_max

        if ao_wav != self._current_ao_wav:
            self._current_ao_wav = ao_wav

    def _set_autosave_parameters(self, prefix, data_dir):

        if not self.inline:
            autosave_on = self.autosave_series.GetValue()
            autosave_choice = self.autosave_choice.GetStringSelection()
        else:
            autosave_on = True
            autosave_choice = self.settings['save_type']

        cmd = ['set_autosave_on', [self.name, autosave_on], {}]

        self._send_cmd(cmd, get_response=False)

        if autosave_on:
            if autosave_choice == 'Absorbance':
                save_raw = False
                save_trans = False
                save_abs = True

            elif autosave_choice == 'Transmission':
                save_raw = False
                save_trans = True
                save_abs = False

            elif autosave_choice == 'Raw':
                save_raw = True
                save_trans = False
                save_abs = False

            elif autosave_choice == 'A & T':
                save_raw = False
                save_trans = True
                save_abs = True

            elif autosave_choice == 'A & T & R':
                save_raw = True
                save_trans = True
                save_abs = True

            elif autosave_choice == 'A & R':
                save_raw = True
                save_trans = False
                save_abs = True

            elif autosave_choice == 'R & T':
                save_raw = True
                save_trans = True
                save_abs = False

            if not self.inline:
                prefix = self.autosave_prefix.GetValue()
                data_dir = self.autosave_dir.GetValue()
                data_dir = os.path.abspath(os.path.expanduser(data_dir))

                # data_dir = data_dir.replace(self.settings['remote_dir_prefix']['local'],
                #     self.settings['remote_dir_prefix']['remote'])

            else:
                data_dir = os.path.abspath(os.path.expanduser(data_dir))
                data_dir = os.path.join(data_dir, self.settings['save_subdir'])

                if not os.path.exists(data_dir):
                    os.makedirs(data_dir)

                data_dir = data_dir.replace(self.settings['remote_dir_prefix']['local'],
                        self.settings['remote_dir_prefix']['remote'])

            kwargs = {
                'save_raw'      : save_raw,
                'save_trans'    : save_trans,
                'save_abs'      : save_abs,
            }

            cmd = ['set_autosave_param', [self.name, data_dir, prefix], kwargs]

            self._send_cmd(cmd, get_response=False)

    def _on_change_dir(self, evt):
        with wx.DirDialog(self, "Select Directory", self.autosave_dir.GetValue()) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return

            pathname = fd.GetPath()

            self.autosave_dir.SetValue(pathname)

    def _show_busy_msg(self):
        wx.MessageBox('Cannot collect spectrum because device is busy.',
            'Device is busy')

    def _set_status(self, cmd, val):
        if cmd == 'set_int_time':
            if str(val) != self.int_time.GetValue():
                self.int_time.SafeChangeValue(str(val))
                self._current_int_time = val

        elif cmd == 'set_scan_avg' and not self.inline:
            if str(val) != self.scan_avg.GetValue():
                self.scan_avg.SafeChangeValue(str(val))

        elif cmd == 'set_smoothing' and not self.inline:
            if str(val) != self.smoothing.GetValue():
                self.smoothing.SafeChangeValue(str(val))

        elif cmd == 'set_xtiming' and not self.inline:
            if str(val) != self.xtiming.GetValue():
                self.xtiming.SafeChangeValue(str(val))

        elif cmd == 'get_hist_time':
            if val != self._history_length:
                self.history_time.SafeChangeValue(str(val))
                self._history_length = val

        elif cmd == 'get_spec_settings':
            int_time = val['int_time']
            scan_avg = val['scan_avg']
            smooth = val['smooth']
            xtiming = val['xtiming']
            dark = val['dark']
            ref = val['ref']
            abs_wavs = val['abs_wavs']
            abs_win = val['abs_win']
            hist_t = val['hist_t']
            ls_shutter = val['ls_shutter']
            ls_uv_lamp = val['ls_uv_lamp']
            ls_vis_lamp = val['ls_vis_lamp']
            wl_range = val['wl_range']
            drift_win = val['drift_win']
            ao_on = val['ao_on']
            ao_v_max = val['ao_v_max']
            ao_au_max = val['ao_au_max']
            ao_wav = val['ao_wavs']
            live_update = val['live_update']

            if not self.inline and str(int_time) != self.int_time.GetValue():
                self.int_time.SafeChangeValue(str(int_time))
            if not self.inline and str(scan_avg) != self.scan_avg.GetValue():
                self.scan_avg.SafeChangeValue(str(scan_avg))
            if not self.inline and str(smooth) != self.smoothing.GetValue():
                self.smoothing.SafeChangeValue(str(smooth))
            if not self.inline and str(xtiming) != self.xtiming.GetValue():
                self.xtiming.SafeChangeValue(str(xtiming))
            if not self.inline and str(ao_au_max) != self.ao_au_max.GetValue():
                self.ao_au_max.SafeChangeValue(str(ao_au_max))
            if (not self.inline and 'out1' in ao_wav
                and str(ao_wav['out1']) != self.ao_out1.GetValue()):
                self.ao_out1.SafeChangeValue(str(ao_wav['out1']))
            if (not self.inline and 'out2' in ao_wav
                and str(ao_wav['out2']) != self.ao_out2.GetValue()):
                self.ao_out2.SafeChangeValue(str(ao_wav['out2']))
            if not self.inline and ao_on != self._current_ao_on:
                self.do_ao.SetValue(ao_on)
            if not self.inline and abs_win != self._current_abs_win:
                self.abs_window.SafeChangeValue(str(abs_win))
            if not self.inline and abs_wavs != self._current_abs_wav:
                self.abs_wavs.SafeChangeValue(', '.join(map(str, abs_wavs)))
            if hist_t != self._history_length:
                self.history_time.SafeChangeValue(str(hist_t))
            if not self.inline and live_update[0] != self.live_update.GetValue():
                self.live_update.SetValue(live_update[0])


            self._history_length = hist_t
            self._current_int_time = int_time
            self._current_scan_avg = scan_avg
            self._current_smooth = smooth
            self._current_xtiming = xtiming
            self._current_abs_wav = abs_wavs
            self._current_abs_win = abs_win
            self._current_wav_range = wl_range
            self._current_drift_win = drift_win
            self._current_ao_on = ao_on
            self._current_ao_v_max = ao_v_max
            self._current_ao_au_max = ao_au_max
            self._current_ao_wav = ao_wav

            self._dark_spectrum = dark
            self._reference_spectrum = ref

            if ls_shutter != self._ls_shutter:
                self._ls_shutter = ls_shutter

                if self._ls_shutter:
                    ls_status = 'Open'
                else:
                    ls_status = 'Closed'

                self.ls_status.SetLabel(ls_status)

            if not self.inline and ls_uv_lamp != self._ls_uv_lamp:
                self._ls_uv_lamp = ls_uv_lamp

                if self._ls_uv_lamp:
                    uv_lamp_status = 'On'
                else:
                    uv_lamp_status = 'Off'

                self.ls_uv_status.SetLabel(uv_lamp_status)

            if not self.inline and ls_vis_lamp != self._ls_vis_lamp:
                self._ls_vis_lamp = ls_vis_lamp

                if self._ls_vis_lamp:
                    vis_lamp_status = 'On'
                else:
                    vis_lamp_status = 'Off'

                self.ls_vis_status.SetLabel(vis_lamp_status)

        elif cmd == 'get_ls_status':
            ls_shutter = val['ls_shutter']
            ls_uv_lamp = val['ls_uv_lamp']
            ls_vis_lamp = val['ls_vis_lamp']

            if ls_shutter != self._ls_shutter:
                self._ls_shutter = ls_shutter

                if self._ls_shutter:
                    ls_status = 'Open'
                else:
                    ls_status = 'Closed'

                self.ls_status.SetLabel(ls_status)

            if not self.inline and ls_uv_lamp != self._ls_uv_lamp:
                self._ls_uv_lamp = ls_uv_lamp

                if self._ls_uv_lamp:
                    uv_lamp_status = 'On'
                else:
                    uv_lamp_status = 'Off'

                self.ls_uv_status.SetLabel(uv_lamp_status)

            if not self.inline and ls_vis_lamp != self._ls_vis_lamp:
                self._ls_vis_lamp = ls_vis_lamp

                if self._ls_vis_lamp:
                    vis_lamp_status = 'On'
                else:
                    vis_lamp_status = 'Off'

                self.ls_vis_status.SetLabel(vis_lamp_status)

        elif cmd == 'collect_spec':
            self._add_new_spectrum(val)

        elif cmd == 'collect_ref':
            self._reference_spectrum = val
            self._add_new_spectrum(val)

        elif cmd == 'collect_dark':
            self._dark_spectrum = val

        elif cmd == 'collect_series':
            self._add_new_spectrum(val)
            self._series_count += 1

            logger.debug('Got series spectrum %s of %s', self._series_count,
                self._series_total)

        elif cmd == 'get_busy':
            if val:
                if self._series_running:
                    msg = ('Collecting {} of {}'.format(self._series_count,
                        self._series_total))
                else:
                    msg = 'Collecting'

            else:
                msg = 'Ready'

            if msg != self.status.GetLabel():
                self.status.SetLabel(msg)

        elif cmd == 'collect_series_start':
            self._series_running = True
            self._series_count = 0
            self._series_total = val

            if self.uv_plot is not None:
                wx.CallAfter(self.uv_plot.set_time_zero)

        elif cmd == 'collect_series_end':
            self._series_running = False
            self._series_count = 0

            # if not self.inline and self._restart_live_update:
            #     self._live_update_evt.set()

        elif cmd == 'get_last_n' and self.auto_update.GetValue():
            specta = val

            for spectrum in val:
                self._add_new_spectrum(spectrum)

    def _add_new_spectrum(self, val):
        self._current_spectrum = val

        if val.spectrum is not None:
            self._add_spectrum_to_history(val)

        if val.trans_spectrum is not None:
            self._add_spectrum_to_history(val, 'trans')

        if val.abs_spectrum is not None:
            self._add_spectrum_to_history(val, 'abs')

        # if self.uv_plot is not None:
        #     self.uv_plot.update_plot_data(val, self._absorbance_history,
        #         self._current_abs_wav)

    def _set_status_commands(self):
        settings_cmd = ['get_spec_settings', [self.name], {}]

        self._update_status_cmd(settings_cmd, 60)

        ls_cmd = ['get_ls_status', [self.name], {}]

        self._update_status_cmd(ls_cmd, 10)

        busy_cmd = ['get_busy', [self.name,], {}]

        self._update_status_cmd(busy_cmd, 1)

    def _add_spectrum_to_history(self, spectrum, spec_type='raw'):
        logger.debug('Adding %s spectrum to history', spec_type)

        if spec_type == 'abs':
            history = self._absorbance_history
        elif spec_type == 'trans':
            history = self._transmission_history
        else:
            history = self._history

        if history is not None:
            history['spectra'].append(spectrum)
            history['timestamps'].append((spectrum.get_timestamp().astimezone() -
                datetime.datetime(1970,1,1,
                tzinfo=datetime.timezone.utc)).total_seconds())

            history = self._prune_history(history)

            if spec_type == 'abs':
                self._absorbance_history = history
            elif spec_type == 'trans':
                self._transmission_history = history
            else:
                self._history = history

    def _prune_history(self, history):
        logger.debug('Pruning history')

        if len(history['timestamps']) > 0:
            now = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime(1970,1,1,
                tzinfo=datetime.timezone.utc)).total_seconds()

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

    def _get_full_history(self):
        abs_cmd = ['get_full_hist_ts', [self.name,], {}]
        trans_cmd = ['get_full_hist_ts', [self.name,], {'spec_type': 'trans'}]
        raw_cmd = ['get_full_hist_ts', [self.name,], {'spec_type': 'raw'}]

        self._absorbance_history = self._send_cmd(abs_cmd, True)
        self._transmission_history = self._send_cmd(trans_cmd, True)
        self._history = self._send_cmd(raw_cmd, True)

    def on_exposure_start(self, exp_panel, trig=1):
        uv_values = None
        uv_valid = True

        if self.collect_uv.GetValue():

            exp_params = exp_panel.current_exposure_values

            prefix = exp_params['fprefix']
            data_dir =  exp_params['local_data_dir']
            exp_time = exp_params['exp_time']
            exp_period = exp_params['exp_period']
            num_frames = exp_params['num_frames']
            num_trig = int(exp_params['num_trig'])

            # if exp_time < 0.125 or exp_period - exp_time < 0.01:
            #     uv_valid = False

            if exp_period < 0.5:
                uv_valid = False

            else:
                max_int_t = float(self.int_time.GetValue())

                # int_time = min(exp_time/2, max_int_t)

                # spec_t = max(int_time*self.settings['int_t_scale'], 0.05)

                # scan_avgs = exp_time // spec_t

                int_time = min((exp_period-0.5)/2, max_int_t)

                spec_t = max(int_time*self.settings['int_t_scale'], 0.05)

                scan_avgs = (exp_period-0.5) // spec_t

                abort_cmd = ['abort_collection', [self.name,], {}]
                self._send_cmd(abort_cmd, get_response=False)

                ext_trig_cmd = ['set_external_trig', [self.name, True], {}]
                self._send_cmd(ext_trig_cmd, get_response=False)

                int_trig_cmd = ['set_int_trig', [self.name, False], {}]
                self._send_cmd(int_trig_cmd, get_response=False)

                # if 'pipeline' in self.settings['components']:
                #     data_dir = os.path.split(data_dir)[0]

                if num_trig > 1:
                    prefix = '{}_{:04}'.format(prefix, trig)

                # Save UV data in the SAXS folder
                if exp_panel.pipeline_ctrl is not None:
                    if 'Experiment type:' in exp_panel.current_metadata:
                        md_exp_type =  exp_panel.current_metadata['Experiment type:']

                        if md_exp_type == 'Batch mode SAXS':
                            if ('Needs Separate Buffer Measurement:' in exp_panel.current_metadata
                                and not exp_panel.current_metadata['Needs Separate Buffer Measurement:']):
                                # batch mode experiments where the running buffer is
                                # good for subtraction can be treated like SEC experiments
                                # in the pipeline
                                exp_type = 'SEC'

                            else:
                                exp_type = 'Batch'

                        elif (md_exp_type == 'SEC-SAXS' or md_exp_type == 'SEC-MALS-SAXS'
                            or md_exp_type == 'AF4-MALS-SAXS'):
                            exp_type = 'SEC'

                        elif md_exp_type == 'TR-SAXS':
                            exp_type = 'TR'

                        else:
                            exp_type = 'Other'

                        if exp_type is not None:
                            data_dir = data_dir.replace(
                                exp_panel.pipeline_ctrl.settings['local_basedir'],
                                exp_panel.pipeline_ctrl.settings['output_basedir'], 1)

                self._set_autosave_parameters(prefix, data_dir)
                valid = self._collect_series(num_frames, int_time, scan_avgs, exp_period, exp_time)

                if valid:
                    while not self._get_busy():
                        time.sleep(0.01)

        return uv_values, uv_valid

    def on_exposure_stop(self, exp_panel):
        if self._get_busy():
            abort_cmd = ['abort_collection', [self.name,], {}]
            self._send_cmd(abort_cmd, get_response=False)

        # self._open_ls_shutter(False)


    def metadata(self):
        metadata = OrderedDict()
        metadata['UV integration time:'] = self._series_exp_time
        metadata['UV scans averaged per spectrum:'] = self._series_scan_avg

        return metadata

    def _on_show_uv_plot(self, evt):
        if self.uvplot_frame is None:
            self.uvplot_frame = UVPlotFrame(self.settings['plot_refresh_t'], self,
                title='UV Plot', size=self._FromDIP((500, 500)))

            self.uv_plot = self.uvplot_frame.uv_plot

            self.uv_plot.update_plot_data(self._current_spectrum,
                self._absorbance_history, self._current_abs_wav)
        else:
            self.uvplot_frame.Raise()

    def _get_plot_data_callback(self):
        return self._current_spectrum, self._absorbance_history, self._current_abs_wav

    def on_uv_frame_close(self):
        self.uvplot_frame = None
        self.uv_plot = None

    def _on_save(self, evt):
        obj = evt.GetEventObject()

        if obj == self.save_current:
            spectrum = self._current_spectrum
        elif obj == self.save_ref:
            spectrum = self._reference_spectrum
        elif obj == self.save_dark:
            spectrum = self._dark_spectrum

        if spectrum is not None:
            self._save_spectrum(spectrum)
        else:
            msg = "The selected spectrum cannot be saved because it doesn't exist."
            wx.CallAfter(wx.MessageBox, msg, 'No Spectrum to save')

    def _save_spectrum(self, spectrum):
        msg = "Please select save directory and enter save file name"
        dialog = wx.FileDialog(self, message=msg,
            style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT, defaultFile='spectrum.csv')

        if dialog.ShowModal() == wx.ID_OK:
            path = dialog.GetPath()
            dialog.Destroy()
        else:
            dialog.Destroy()
            return

        path=os.path.splitext(path)[0]
        savedir, name = os.path.split(path)

        # spectrum.save_spectrum(name, savedir)

        if spectrum.spectrum is not None:
            spectrum.save_spectrum(name+'_raw.csv', savedir, 'raw')

        if spectrum.trans_spectrum is not None:
            spectrum.save_spectrum(name+'_trans.csv', savedir, 'trans')

        if spectrum.abs_spectrum is not None:
            spectrum.save_spectrum(name+'.csv', savedir, 'abs')

    def _on_close(self):
        """Device specific stuff goes here"""

        # if not self.inline:
        #     self._live_update_stop.set()
        #     self._live_update_thread.join()

    def on_exit(self):
        self.close()


class UVPlot(wx.Panel):

    def __init__(self, data_callback, refresh_time=1, *args, **kwargs):

        super(UVPlot, self).__init__(*args, **kwargs)

        self.data_callback = data_callback

        self.plot_type = 'Spectrum'
        self.spectrum_type = 'abs'

        self.spectrum = None
        self.abs_history = None
        self.abs_wvl = None
        self.abs_data = None

        self.spectrum_line = None
        self.abs_lines = []

        self._time_window = 10
        self._time_zero = time.time()

        self._refresh_time = refresh_time
        self._last_refresh = 0
        self._needs_refresh = True
        self._updating_plot = False

        self._create_layout()

        # Connect the callback for the draw_event so that window resizing works:
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)
        self.canvas.mpl_connect('motion_notify_event', self._onMouseMotionEvent)

        self.refresh_timer = wx.Timer()
        self.refresh_timer.Bind(wx.EVT_TIMER, self._on_refresh_timer)
        self.refresh_timer.Start(int(self._refresh_time*1000))


    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):

        plot_parent = self

        self.plot_type_ctrl = wx.Choice(plot_parent, choices=['Spectrum',
            'Absorbance'])
        self.plot_type_ctrl.SetStringSelection(self.plot_type)
        self.plot_type_ctrl.Bind(wx.EVT_CHOICE, self._on_plot_type)

        self.spectrum_type_ctrl = wx.Choice(plot_parent, choices=['Absorbance',
            'Transmission', 'Raw'])
        self.spectrum_type_ctrl.SetStringSelection('Absorbance')
        self.spectrum_type_ctrl.Bind(wx.EVT_CHOICE, self._on_spectrum_type)

        self.t_window = utils.ValueEntry(self._on_twindow_change,
            plot_parent, validator=utils.CharValidator('float_te'),
            size=self._FromDIP((50,-1)))
        self.t_window.ChangeValue(str(self._time_window))
        self.t_window.Disable()

        self.zero_time = wx.Button(plot_parent, label='Zero Time')
        self.zero_time.Bind(wx.EVT_BUTTON, self._on_zero_time)
        self.zero_time.Disable()

        self.auto_limits = wx.CheckBox(plot_parent, label='Auto Limits')
        self.auto_limits.SetValue(True)

        ctrl_sizer = wx.FlexGridSizer(cols=4, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        ctrl_sizer.Add(wx.StaticText(plot_parent, label='Plot type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.plot_type_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(wx.StaticText(plot_parent, label='Spectrum type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.spectrum_type_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(wx.StaticText(plot_parent, label='Time range [min]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.t_window, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.auto_limits, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.zero_time, flag=wx.ALIGN_CENTER_VERTICAL)

        self.fig = Figure((5,4), 75)

        self.subplot = self.fig.add_subplot(1,1,1)
        self.subplot.set_xlabel('Wavelength [nm]')
        self.subplot.set_ylabel('Absorbance [mAu]')

        # self.fig.subplots_adjust(left = 0.13, bottom = 0.1, right = 0.93,
        #     top = 0.93, hspace = 0.26)
        self.fig.set_facecolor('white')

        self.canvas = FigureCanvasWxAgg(plot_parent, wx.ID_ANY, self.fig)
        self.canvas.SetBackgroundColour('white')

        self.background = self.canvas.copy_from_bbox(self.subplot.bbox)

        self.toolbar = utils.CustomPlotToolbar(self.canvas)
        self.toolbar.Realize()

        plot_sizer = wx.BoxSizer(wx.VERTICAL)
        plot_sizer.Add(ctrl_sizer, flag=wx.EXPAND)
        plot_sizer.Add(self.canvas, proportion=1, flag=wx.EXPAND)
        plot_sizer.Add(self.toolbar, proportion=0, flag=wx.EXPAND)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(plot_sizer, proportion=1, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.TOP)
        self.SetSizer(top_sizer)

    def _on_plot_type(self, evt):
        self.plot_type = self.plot_type_ctrl.GetStringSelection()

        if self.plot_type == 'Spectrum':
            self.subplot.set_xlabel('Wavelength [nm]')
            self.spectrum_type_ctrl.Enable()
            self.t_window.Disable()
            self.zero_time.Disable()
        elif self.plot_type == 'Absorbance':
            self.subplot.set_xlabel('Time [min]')
            self.subplot.set_ylabel('Absorbance [mAu]')
            self.spectrum_type_ctrl.Disable()
            self.t_window.Enable()
            self.zero_time.Enable()

        self._updating_plot = True
        wx.CallAfter(self.plot_data)

    def _on_spectrum_type(self, evt):
        stype = self.spectrum_type_ctrl.GetStringSelection()

        if stype == 'Absorbance':
            self.spectrum_type = 'abs'
            self.subplot.set_ylabel('Absorbance [mAu]')
        elif stype == 'Transmission':
            self.spectrum_type = 'trans'
            self.subplot.set_ylabel('Transmission')
        elif stype == 'Raw':
            self.spectrum_type = 'raw'
            self.subplot.set_ylabel('Raw')

        self._updating_plot = True
        wx.CallAfter(self.plot_data)

    def _on_twindow_change(self, obj, val):
        self._time_window = float(val)

        self.canvas.mpl_disconnect(self.cid)
        self._updating_plot = True
        wx.CallAfter(self.updatePlot)
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

    def _on_zero_time(self, evt):
        self.set_time_zero()

    def set_time_zero(self):
        self._time_zero = time.time()
        # self.update_plot_data(self.spectrum, self.abs_history, self.abs_wvl, True)

    def _on_refresh_timer(self, evt):
        # a = time.time()
        if self._needs_refresh:
            if (time.time() - self._last_refresh > self._refresh_time
                and not self._updating_plot):
                self._updating_plot = True
                self.update_plot_data()
                wx.CallAfter(self.plot_data)
                self._last_refresh = time.time()
                # self._needs_refresh = False
        # print(time.time()-a)

    # def update_plot_data(self, spectrum, abs_history, abs_wvl, force_refresh=False):
    #     # print('updating_plot_data')
    #     # a = time.time()

    #     self.spectrum = spectrum
    #     self.abs_history = abs_history
    #     self.abs_wvl = abs_wvl

    #     if abs_history is not None and len(abs_history['spectra']) > 0:
    #         current_time = time.time()
    #         timestamps = np.array(abs_history['timestamps'])
    #         time_data = (timestamps - self._time_zero)/60

    #         abs_data = []

    #         if abs_wvl is not None:
    #             for wvl in abs_wvl:
    #                 spec_abs = [spectra.get_absorbance(wvl) for spectra in
    #                         abs_history['spectra']]
    #                 abs_data.append([time_data, np.array(spec_abs), str(wvl)])
    #     else:
    #         abs_data = []

    #     self.abs_data = abs_data
    #     # print(time.time()-a)

    #     if not force_refresh:
    #         self._needs_refresh = True
    #     else:
    #         self._updating_plot = True
    #         wx.CallAfter(self.plot_data)

    def update_plot_data(self):
        # print('updating_plot_data')
        # a = time.time()

        spectrum, abs_history, abs_wvl = self.data_callback()

        self.spectrum = spectrum
        self.abs_history = abs_history
        self.abs_wvl = abs_wvl

        if abs_history is not None and len(abs_history['spectra']) > 0:
            current_time = time.time()
            timestamps = np.array(abs_history['timestamps'])
            time_data = (timestamps - self._time_zero)/60

            abs_data = []

            if abs_wvl is not None:
                for wvl in abs_wvl:
                    spec_abs = [spectra.get_absorbance(wvl) for spectra in
                            abs_history['spectra']]
                    abs_data.append([time_data, np.array(spec_abs), str(wvl)])
        else:
            abs_data = []

        self.abs_data = abs_data
        # print(time.time()-a)

        # if not force_refresh:
        #     self._needs_refresh = True
        # else:
        #     self._updating_plot = True
        #     wx.CallAfter(self.plot_data)

    def ax_redraw(self, widget=None):
        ''' Redraw plots on window resize event '''
        self.background = self.canvas.copy_from_bbox(self.subplot.bbox)

        self.canvas.mpl_disconnect(self.cid)
        self.updatePlot()
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

    def plot_data(self):
        self.canvas.mpl_disconnect(self.cid)

        if self.plot_type == 'Spectrum':
            if self.spectrum is not None:
                data  = self.spectrum.get_spectrum(self.spectrum_type)

                if data is not None:
                    xdata = data[:, 0]
                    spectrum_data = data[:, 1]

                    if self.spectrum_line is not None:
                        self.spectrum_line.set_visible(True)

                else:
                    spectrum_data = None

            else:
                spectrum_data = None

            for line in self.abs_lines:
                line.set_visible(False)

        elif self.plot_type == 'Absorbance':
            abs_ydata = []
            abs_labels = []
            if self.abs_data is not None:

                for i, data in enumerate(self.abs_data):
                    xdata = data[0]
                    abs_ydata.append(data[1])
                    abs_labels.append(data[2])

                for line in self.abs_lines:
                    line.set_visible(True)

            if self.spectrum_line is not None:
                self.spectrum_line.set_visible(False)

        redraw = False

        if self.plot_type == 'Spectrum' and spectrum_data is not None:
            if self.spectrum_line is None:
                self.spectrum_line, = self.subplot.plot(xdata, spectrum_data,
                    animated=True)
                redraw = True
            else:
                self.spectrum_line.set_xdata(xdata)
                self.spectrum_line.set_ydata(spectrum_data)

            legend = self.subplot.get_legend()

            if legend is not None:
                legend.remove()
                redraw=True

        elif self.plot_type == 'Absorbance' and len(abs_ydata) > 0:
            if len(self.abs_lines) > len(self.abs_data):
                for i in range(len(self.abs_data), len(self.abs_lines)):
                    line = self.abs_lines.pop()
                    line.remove()
                    redraw = True

            for i, ydata in enumerate(abs_ydata):
                if len(ydata) != len(xdata):
                    if len(ydata) > len(xdata):
                        ydata = ydata[:len(xdata)]
                    else:
                        xdata = xdata[:len(ydata)]
                if i < len(self.abs_lines):
                    line = self.abs_lines[i]
                    line.set_xdata(xdata)
                    line.set_ydata(ydata)

                    label = line.get_label()
                    if label != abs_labels[i]:
                        line.set_label(abs_labels[i])
                        redraw = True
                else:
                    line, = self.subplot.plot(xdata, ydata, animated=True,
                        label=abs_labels[i]+' nm')

                    self.abs_lines.append(line)
                    redraw = True

            legend = self.subplot.get_legend()

            if legend is None:
                self.subplot.legend()
                redraw=True
            else:
                leg_lines = self.subplot.get_legend().get_lines()
                if len(leg_lines) == len(self.abs_lines):
                    for i in range(len(leg_lines)):
                        if self.abs_lines[i].get_label() != leg_lines[i].get_label():
                            redraw = True
                            break
                else:
                    redraw=True

        if redraw:
            if self.auto_limits.GetValue():
                self.fig.tight_layout()
            self.canvas.draw()
            self.background = self.canvas.copy_from_bbox(self.subplot.bbox)

        self.updatePlot()

        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

    def updatePlot(self, redraw=False):

        if self.auto_limits.GetValue():
            oldx = self.subplot.get_xlim()
            oldy = self.subplot.get_ylim()

            if self.spectrum_line is not None and self.plot_type == 'Spectrum':
                xdata = self.spectrum_line.get_xdata()
                ydata = self.spectrum_line.get_ydata()

                xmin = min(xdata[np.isfinite(xdata)])
                xmax = max(xdata[np.isfinite(xdata)])


                if xmin != oldx[0] or xmax != oldx[1]:
                    newx = [xmin, xmax]

                else:
                    newx = oldx

                _, start_idx = utils.find_closest(newx[0], xdata)
                _, end_idx = utils.find_closest(newx[1], xdata)

                ymin = min(ydata[np.isfinite(ydata[start_idx:end_idx+1])])
                ymax = max(ydata[np.isfinite(ydata[start_idx:end_idx+1])])

                offset = abs(ymax - ymin)*0.05

                if ymin < oldy[0] or oldy[0] < ymin-offset:
                        new_ymin = ymin-offset
                else:
                    new_ymin = oldy[0]

                if ymax > oldy[1] or oldy[1] > ymax+offset:
                        new_ymax = ymax+offset
                else:
                    new_ymax = oldy[1]

                newy = [new_ymin, new_ymax]

            elif len(self.abs_lines) > 0 and self.plot_type == 'Absorbance':
                cur_xmin = None
                cur_xmax = None
                cur_ymin = None
                cur_ymax = None

                for line in self.abs_lines:
                    xdata = np.array(line.get_xdata())

                    data_range = xdata[np.isfinite(xdata)]
                    xmin = min(data_range)
                    xmax = max(data_range)

                    if cur_xmin is not None:
                        cur_xmin = min(xmin, cur_xmin)
                    else:
                        cur_xmin = xmin

                    if cur_xmax is not None:
                        cur_xmax = max(xmax, cur_xmax)
                    else:
                        cur_xmax = xmax

                if (cur_xmax > oldx[1] or (oldx[1] - oldx[0] != self._time_window*1.1
                    and oldx[1] - oldx[0] != self._time_window)):
                    new_trange = True
                else:
                    new_trange = False

                if new_trange:
                    if cur_xmax - cur_xmin < self._time_window:
                        new_xmin = cur_xmin
                        new_xmax = cur_xmin + self._time_window
                    else:
                        new_xmax = cur_xmax + self._time_window*0.1
                        new_xmin = new_xmax - self._time_window

                else:
                    new_xmin = oldx[0]
                    new_xmax = oldx[1]

                newx = [new_xmin, new_xmax]

                _, start_idx = utils.find_closest(newx[0], xdata)
                _, end_idx = utils.find_closest(newx[1], xdata)

                for line in self.abs_lines:
                    ydata = np.array(line.get_ydata())

                    data_range = ydata[start_idx:end_idx+1][np.isfinite(ydata[start_idx:end_idx+1])]

                    if len(data_range) > 0:
                        ymin = min(data_range)
                        ymax = max(data_range)

                        if cur_ymin is not None:
                            cur_ymin = min(ymin, cur_ymin)
                        else:
                            cur_ymin = ymin

                        if cur_ymax is not None:
                            cur_ymax = max(ymax, cur_ymax)
                        else:
                            cur_ymax = ymax

                if cur_ymin is not None:
                    offset = abs(cur_ymax - cur_ymin)*0.05

                    if cur_ymin < oldy[0] or oldy[0] < cur_ymin-offset:
                            new_ymin = cur_ymin-offset
                    else:
                        new_ymin = oldy[0]

                    if cur_ymax > oldy[1] or oldy[1] > cur_ymax+offset:
                            new_ymax = cur_ymax+offset
                    else:
                        new_ymax = oldy[1]

                    newy = [new_ymin, new_ymax]

                else:
                    newy = oldy

            else:
                self.subplot.relim()
                self.subplot.autoscale_view()

                newx = self.subplot.get_xlim()
                newy = self.subplot.get_ylim()

            if newx[0] != oldx[0] or newx[1] != oldx[1] or newy[0] != oldy[0] or newy[1] != oldy[1]:
                self.subplot.set_xlim(newx)
                self.subplot.set_ylim(newy)
                redraw = True

        if redraw:
            if self.auto_limits.GetValue():
                self.fig.tight_layout()
            self.canvas.draw()
            self.background = self.canvas.copy_from_bbox(self.subplot.bbox)

        self.canvas.restore_region(self.background)

        if self.spectrum_line is not None and self.plot_type == 'Spectrum':
            self.subplot.draw_artist(self.spectrum_line)
        elif len(self.abs_lines) > 0 and self.plot_type == 'Absorbance':
            for line in self.abs_lines:
                self.subplot.draw_artist(line)

        self.canvas.blit(self.subplot.bbox)

        self._updating_plot = False

    def _onMouseMotionEvent(self, event):
        if event.inaxes:
            x, y = event.xdata, event.ydata
            xlabel = self.subplot.xaxis.get_label().get_text()
            ylabel = self.subplot.yaxis.get_label().get_text()

            if abs(y) > 0.001 and abs(y) < 1000:
                y_val = '{:.3f}'.format(round(y, 3))
            else:
                y_val = '{:.3E}'.format(y)

            self.toolbar.set_status('{} = {}, {} = {}'.format(xlabel, round(x, 3), ylabel, y_val))

        else:
            self.toolbar.set_status('')

class UVPlotFrame(wx.Frame):
    def __init__(self, plot_refresh_t=1, *args, **kwargs):
        super(UVPlotFrame, self).__init__(*args, **kwargs)

        self._create_layout(plot_refresh_t)

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self.Raise()
        self.Show()

    def _create_layout(self, plot_refresh_t):
        self.uv_plot = UVPlot(plot_refresh_t, self)

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.uv_plot, 1, flag=wx.EXPAND)

        self.SetSizer(sizer)

    def _on_exit(self, evt):
        self.GetParent().on_uv_frame_close()
        self.Destroy()

class UVFrame(utils.DeviceFrame):

    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(UVFrame, self).__init__(name, settings, UVPanel, *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()





default_spectrometer_settings = {
        'device_init'           : [{'name': 'CoflowUV', 'args': ['StellarNet', None],
                                    'kwargs': {'shutter_pv_name': '18ID:E1608:Bo1',
                                    'trigger_pv_name' : '18ID:LJT4:2:Bo12',
                                    'out1_pv_name' : '18ID:E1608:Ao1',
                                    'out2_pv_name' : '18ID:E1608:Ao2',
                                    'trigger_in_pv_name' : '18ID:E1608:Bi8',
                                    'deut_lamp_ctrl_pv_name' : '18ID:E1608:Bo2',
                                    'hal_lamp_ctrl_pv_name' : '18ID:E1608:Bo3',
                                    'deut_lamp_status_pv_name' : '18ID:E1608:Bi4',
                                    'hal_lamp_status_pv_name' : '18ID:E1608:Bi5',}},],
        'max_int_t'             : 0.025, # in s
        'scan_avg'              : 1,
        'smoothing'             : 0,
        'xtiming'               : 3,
        'spectrum_type'         : 'Absorbance', #Absorbance, Transmission, Raw
        'dark_correct'          : True,
        'auto_dark'             : True,
        'auto_dark_t'           : 60*60, #in s
        'dark_avgs'             : 3,
        'ref_avgs'              : 2,
        'history_t'             : 60*60, #in s
        'save_subdir'           : 'UV',
        'save_type'             : 'Absorbance',
        'series_ref_at_start'   : True,
        'drift_correct'         : False,
        'drift_window'          : [750, 800],
        'abs_wav'               : [280, 260],
        'abs_window'            : 3,
        'int_t_scale'           : 2,
        'wavelength_range'      : [225, 838.39],
        'analog_out_v_max'      : 10.,
        'analog_out_au_max'     : 10000, #mAu
        'analog_out_wav'        : {'out1': 280, 'out2': 260},
        'do_ao'                 : True,
        'remote_ip'             : '164.54.204.53',
        'remote_port'           : '5559',
        'device_communication'  : 'local',
        'remote'                : False,
        'remote_device'         : 'uv',
        'com_thread'            : None,
        'remote_dir_prefix'     : {'local' : '/nas_data', 'remote' : 'Z:\\'},
        'inline_panel'          : False,
        'plot_refresh_t'        : 0.1, #in s
    }


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

    # spec = StellarnetUVVis('Test')
    # spec.collect_dark()
    # spec.collect_reference_spectrum()
    # spec.disconnect()

    # cmd_q = deque()
    # ret_q = deque()
    # status_q = deque()

    # com_thread.add_new_communication('test_com', cmd_q, ret_q, status_q)

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

    # com_thread.add_new_communication('test_com2', cmd_q2, ret_q2, status_q2)

    # get_int_time_cmd = ['get_int_time', ['Test2'], {}]
    # cmd_q.append(get_int_time_cmd)

    # get_scan_avg_cmd = ['get_scan_avg', ['Test2', 1], {}]
    # cmd_q2.append(get_scan_avg_cmd)

    # get_int_status_cmd = ['get_int_time', ['Test2',], {}]
    # com_thread.add_status_cmd(get_int_status_cmd, 10)

    # Local
    com_thread = UVCommThread('UvComm')
    com_thread.start()

    # Remote
    # com_thread = None

    spectrometer_settings = default_spectrometer_settings
    spectrometer_settings['components'] = ['uv']
    spectrometer_settings['com_thread'] = com_thread


    #initialized epics ca context in the main thread first as recommended
    # epics.get_pv(spectrometer_settings['device_init'][0]['kwargs']['shutter_pv_name'])

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = UVFrame('UVFrame', spectrometer_settings, parent=None,
        title='UV Spectrometer Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()

    """
    To do:
    Test shutter open/close when taking images and darks
    Set absorbance wavelengths/window in GUI
    Add plotting to GUI
    Need to be able to get metadata
    Open and close lightsource shutter in GUI
    """
