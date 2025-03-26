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
import collections
import traceback
import logging
import sys
import copy
import platform
import os
import multiprocessing

import zmq

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

pipeline_path = os.path.abspath(os.path.expanduser('~//saxs-pipeline'))
if pipeline_path not in os.sys.path:
    os.sys.path.append(pipeline_path)

try:
    import pipeline.client.ControlClient as Client
except Exception:
    pass
    # This is a hack for python 2! Because the pipeline isn't written to be
    # pipeline 2 compatible, but MX isn't python 3 compatible yet!

class PipelineControl(object):

    def __init__(self, settings):
        self.settings = settings
        self.port = self.settings['server_port']
        self.ip = self.settings['server_ip']

        self.cmd_q = deque()
        self.return_q = deque()
        self.abort_event = threading.Event()
        self.timeout_event = threading.Event()

        try:
            self.control_client = Client(self.ip, self.port,
                self.cmd_q, self.return_q, self.abort_event, self.timeout_event,
                name='PipelineCtrlClient')
        except Exception:
            # This is a hack for python 2! Because the pipeline isn't written to be
            # pipeline 2 compatible, but MX isn't python 3 compatible yet!
            self.control_client = ControlClient(self.ip, self.port,
                self.cmd_q, self.return_q, self.abort_event, self.timeout_event,
                name='PipelineCtrlClient')
        self.control_client.start()

        # self.set_raw_settings(self.settings['raw_settings'])

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

        if (self.settings['detector'].lower() == 'eiger' 
            and self.settings['data_source'].lower() == 'file'):
            fprefix = '{}_data_'.format(fprefix)

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

        elif exp_type == 'TR':
            cmd_kwargs = {'num_exps': n_exps}

        elif exp_type == 'Other':
            cmd_kwargs = {'num_exps': n_exps}

        pipeline_data_dir = data_dir.replace(self.settings['local_basedir'], 
                self.settings['data_basedir'], 1)

        output_dir = os.path.split(data_dir)[0]
        pipeline_output_dir = output_dir.replace(self.settings['local_basedir'], 
                self.settings['output_basedir'], 1)

        cmd = ('start_experiment', [exp_name, exp_type, pipeline_data_dir, fprefix, 
            pipeline_output_dir], cmd_kwargs)

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
            cmd = ('stop_experiment', [self.current_expeirment,], {})
            client_cmd = {'command': cmd, 'response': False}
            self.cmd_q.append(client_cmd)

    def set_raw_settings(self, settings_file):
        """
        Pipeline loads a new RAW settings file
        settings_file - The settings file to load (must be accessible to pipeline,
            and path must be on pipeline computer)
        """
        cmd = ('load_raw_settings', [settings_file,], {})
        client_cmd = {'command': cmd, 'response': False}
        self.cmd_q.append(client_cmd)

    def stop(self):
        """
        Stops client cleanly
        """
        self.control_client.stop()
        self.control_client.join(5)

class ControlClient(threading.Thread):
    """

    """

    def __init__(self, ip, port, command_queue, answer_queue, abort_event,
        timeout_event, name='ControlClient'):
        """
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting control client: %s", self.name)

        self.ip = ip
        self.port = port
        self.command_queue = command_queue
        self.answer_queue = answer_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()
        self.timeout_event = timeout_event

        self.connect_error = 0

        self.heartbeat = 60
        self.last_ping = 0

        self.resend_missed_commands_on_reconnect = True
        self.missed_cmds = collections.deque()

    def run(self):
        """
        Custom run method for the thread.
        """

        logger.info("Connecting to %s on port %s", self.ip, self.port)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.set(zmq.LINGER, 0)
        self.socket.connect("tcp://{}:{}".format(self.ip, self.port))

        while True:
            try:
                if not self.socket.closed:
                    if time.time() - self.last_ping > self.heartbeat:
                        self.last_ping = time.time()
                        self._ping()
                else:
                    if self.socket.closed:
                        self._ping()

                if len(self.command_queue) > 0:
                    # logger.debug("Getting new command")
                    command = self.command_queue.popleft()
                else:
                    command = None

                if self._abort_event.is_set():
                    logger.debug("Abort event detected")
                    self._abort()
                    command = None

                if self._stop_event.is_set():
                    logger.debug("Stop event detected")
                    break

                if command is not None:
                    # logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ", device, cmd[0], ', '.join(['{}'.format(a) for a in cmd[1]]), ', '.join(['{}:{}'.format(kw, item) for kw, item in cmd[2].items()]))

                    if not self.socket.closed:
                        self._send_cmd(command)

                    elif self.resend_missed_commands_on_reconnect:
                        self.missed_cmds.append(command)

                else:
                    time.sleep(0.01)

            except Exception:
                logger.error('Error in client thread:\n{}'.format(traceback.format_exc()))

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        if not self.socket.closed:
            self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
            self.socket.close(0)

        self.context.destroy(0)
        logger.info("Quitting remote client thread: %s", self.name)

    def _send_cmd(self, command):
        cmd = command['command']
        get_response = command['response']

        try:
            self.socket.send_json(command)

            start_time = time.time()
            while self.socket.poll(10) == 0 and time.time()-start_time < 5:
                pass

            if self.socket.poll(10) > 0:
                answer = self.socket.recv_json()
            else:
                answer = ''

            if answer == '':
                raise zmq.ZMQError(msg="Could not get a response from the server")
            else:
                self.connect_error = 0

            # logger.debug('Command response: %s' %(answer))

            if get_response:
                self.answer_queue.append(answer)

        except zmq.ZMQError:
            cmd = command['command']
            msg = ("Pipeline failed to run command '%s' with args: %s and "
                "kwargs: %s. Timeout or other ZMQ error." %(cmd[0],
                ', '.join(['{}'.format(a) for a in cmd[1]]),
                ', '.join(['{}:{}'.format(kw, item) for kw, item in cmd[2].items()])))
            logger.error(msg)
            self.connect_error += 1
            self._ping()
            if not self.timeout_event.set():
                self.answer_queue.append(None)

            self.missed_cmds.append(command)

        except Exception:
            cmd = command['command']
            msg = ("Pipeline failed to run command '%s' with args: %s "
                "and kwargs: %s. Exception follows:" %(cmd[0],
                ', '.join(['{}'.format(a) for a in cmd[1]]),
                ', '.join(['{}:{}'.format(kw, item) for kw, item in cmd[2].items()])))
            logger.error(msg)
            logger.error(traceback.print_exc())
            self.connect_error += 1

            self.missed_cmds.append(command)

        if self.connect_error > 5 and not self.timeout_event.is_set():
            msg = ('5 consecutive failures to run a pipeline command.')
            logger.error(msg)
            logger.error("Connection timed out")
            self.timeout_event.set()

    def _ping(self):
        # logger.debug("Checking if server is active")
        cmd = {'device': 'server', 'command': ('ping', (), {}), 'response': False}

        connect_tries = 0

        if not self.socket.closed:
            while connect_tries < 5:
                self.socket.send_json(cmd)

                start_time = time.time()
                while self.socket.poll(10) == 0 and time.time()-start_time < 1:
                    pass

                if self.socket.poll(10) > 0:
                    answer = self.socket.recv_json()
                else:
                    answer = ''

                if answer == 'ping received':
                    logger.debug("Connection to server verified")
                    connect_tries = 5
                else:
                    logger.error("Could not get a response from the server")
                    connect_tries = connect_tries+1

                    if connect_tries == 5:
                        logger.error("Connection timed out")
                        self.timeout_event.set()
                        self.connect_error = 6
                        self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
                        self.socket.close(0)

        else:
            self.socket = self.context.socket(zmq.PAIR)
            self.socket.set(zmq.LINGER, 0)
            self.socket.connect("tcp://{}:{}".format(self.ip, self.port))

            while connect_tries < 5:
                self.socket.send_json(cmd)

                start_time = time.time()
                while self.socket.poll(10) == 0 and time.time()-start_time < 0.1:
                    pass

                if self.socket.poll(10) > 0:
                    answer = self.socket.recv_json()
                else:
                    answer = ''

                if answer == 'ping received':
                    logger.debug("Connection to server verified")
                    connect_tries = 5
                    self.timeout_event.clear()
                    self.connect_error = 0

                    if self.resend_missed_commands_on_reconnect:
                        while len(self.missed_cmds) > 0 and not self.timeout_event.is_set():
                            cmd = self.missed_cmds.popleft()
                            self._send_cmd(cmd)

                else:
                    connect_tries = connect_tries+1

            if self.timeout_event.is_set():
                self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
                self.socket.close(0)



    def _abort(self):
        logger.info("Aborting remote client thread %s current and future commands", self.name)
        self.command_queue.clear()
        self._abort_event.clear()
        logger.debug("Remote client thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down remote client thread: %s", self.name)

        self._stop_event.set()

if __name__ == '__main__':
    # multiprocessing.set_start_method('spawn')

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
        # 'raw_settings'  : '../data/UO_SEC/SAXS.cfg',
        'local_basedir' : '/nas_data/Eiger2xe9M',
        'data_basedir'  : '/nas_data/Eiger2xe9M',
        'output_basedir': '/nas_data/SAXS',
        }


