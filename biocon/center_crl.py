import time

import numpy as np
import scipy
import epics

import XPS_C8_drivers as xps_drivers

import motorcon

def do_centering_scan(scan_settings):
    start = scan_settings['start']
    stop = scan_settings['stop']
    step = scan_settings['step']
    meas_pv = scan_settings['meas_pv']
    count_time = scan_settings['count_time_pv']
    count_start = scan_settings['count_start_pv']
    meas_time = scan_settings['meas_time']
    fw_height = scan_settings['fw_height']
    center_offset = scan_settings['center_offset']
    shutter_pvs = scan_settings['shutter_pvs']

    motor = scan_settings['motor']
    scan_positioner = scan_settings['positioner']
    scan_mindex = scan_settings['motor_index']

    initial_pos = motor.get_positioner_position(scan_positioner, scan_mindex)

    start += initial_pos
    stop += initial_pos

    motor.move_positioner_absolute(scan_positioner, scan_mindex, start)

    if start < stop:
        mtr1_positions = np.arange(start, stop+step, step)
    else:
        mtr1_positions = np.arange(stop, start+step, step)
        mtr1_positions = mtr1_positions[::-1]

    count_time.put(meas_time)

    scaler_vals = np.zeros_like(mtr1_positions)

    for shutter in shutter_pvs:
        open_val = shutter['open']
        pv = shutter['pv']
        pv.put(open_val)

    for num, mtr1_pos in enumerate(mtr1_positions):
        if mtr1_pos != mtr1_positions[0]:
            # logger.info('Moving motor 1 position to {}'.format(mtr1_pos))
            motor.move_positioner_absolute(scan_positioner,
                scan_mindex, mtr1_pos)
        # mtr1.wait_for_motor_stop()
        # if not motor.is_moving():
        #     while not motor.is_moving():
        #         time.sleep(0.01)
        #         if self._centering_abort_event.is_set():
        #             motor.stop()
        #             for shutter in shutter_pvs:
        #                 close_val = shutter['close']
        #                 pv = shutter['pv']
        #                 pv.put(close_val)
        #             wx.CallAfter(self.run_centering.SetLabel, 'Center Mixer')
        #             return


        while motor.is_moving():
            time.sleep(0.01)

        count_start.put(1,wait=True)

        while count_start.get() != 0:
            time.sleep(0.01)

        counts = meas_pv.get()
        scaler_vals[num] = counts
        print('Mtr: {} Cts: {}'.format(mtr1_pos, counts))

    for shutter in shutter_pvs:
        close_val = shutter['close']
        pv = shutter['pv']
        pv.put(close_val)

    center, fwhm = calc_fw_position(mtr1_positions, scaler_vals,
        fw_height)

    print('Found {} center at: {}'.format(scan_positioner, center))

    center += center_offset
    center = round(center, 6)

    print('Setting {} center at: {}'.format(scan_positioner, center))

    motor.move_positioner_absolute(scan_positioner, scan_mindex, center)

    while motor.is_moving():
        time.sleep(0.01)

    return center

def calc_fw_position(mtr_pos, scaler_vals, fw_height):
    """
    FW height is the value at which to calulcate the FW. So fw_height
    of 0.5 calcultes FW half max, a fw_height of 0.25 would be FW quarter max,
    and so on.
    """
    if mtr_pos is not None and len(mtr_pos)>3:
        y = scaler_vals - np.max(scaler_vals)*fw_height
        if mtr_pos[0]>mtr_pos[1]:
            spline = scipy.interpolate.UnivariateSpline(mtr_pos[::-1], y[::-1], s=0)
        else:
            spline = scipy.interpolate.UnivariateSpline(mtr_pos, y, s=0)

        try:
            roots = spline.roots()
            if roots.size == 2:
                r1 = roots[0]
                r2 = roots[1]

                if mtr_pos[1]>mtr_pos[0]:
                    if r1>r2:
                        index1 = np.searchsorted(mtr_pos, r1, side='right')
                        index2 = np.searchsorted(mtr_pos, r2, side='right')
                    else:
                        index1 = np.searchsorted(mtr_pos, r2, side='right')
                        index2 = np.searchsorted(mtr_pos, r1, side='right')

                    mean = np.mean(y[index1:index2])
                else:
                    if r1>r2:
                        index1 = np.searchsorted(mtr_pos[::-1], r1, side='right')
                        index2 = np.searchsorted(mtr_pos[::-1], r2, side='right')
                    else:
                        index1 = np.searchsorted(mtr_pos[::-1], r2, side='right')
                        index2 = np.searchsorted(mtr_pos[::-1], r1, side='right')

                    mean = np.mean(y[::-1][index1:index2])

                if mean<=0:
                    r1 = 0
                    r2 = 0

            elif roots.size>2:
                max_diffs = np.argsort(abs(np.diff(roots)))[::-1]
                for rmax in max_diffs:
                    r1 = roots[rmax]
                    r2 = roots[rmax+1]

                    if mtr_pos[1]>mtr_pos[0]:
                        if r1<r2:
                            index1 = np.searchsorted(mtr_pos, r1, side='right')
                            index2 = np.searchsorted(mtr_pos, r2, side='right')
                        else:
                            index1 = np.searchsorted(mtr_pos, r2, side='right')
                            index2 = np.searchsorted(mtr_pos, r1, side='right')

                        mean = np.mean(y[index1:index2])
                    else:
                        if r1<r2:
                            index1 = np.searchsorted(mtr_pos[::-1], r1, side='right')
                            index2 = np.searchsorted(mtr_pos[::-1], r2, side='right')
                        else:
                            index1 = np.searchsorted(mtr_pos[::-1], r2, side='right')
                            index2 = np.searchsorted(mtr_pos[::-1], r1, side='right')

                        mean = np.mean(y[::-1][index1:index2])

                    if mean>0:
                        break
            else:
                r1 = 0
                r2 = 0
        except Exception:
          r1 = 0
          r2 = 0

        fwhm = np.fabs(r2-r1)

        if r1 < r2:
            center = r1 + fwhm/2.
        else:
            center = r2 + fwhm/2.

    return center, fwhm


if __name__ == 'main':

    xps = xps_drivers.XPS()
    np_motor = motorcon.NewportXPSMotor('HEXAPOD', xps,
                '164.54.204.49', 5001, 20, 'HEXAPOD', 6, True)

    motors = ['HEXAPOD.X', 'HEXAPOD.Y', 'HEXAPOD.Z', 'HEXAPOD.U',
        'HEXAPOD.V', 'HEXAPOD.W']
    index_ref = {'HEXAPOD.X': 0, 'HEXAPOD.Y': 1, 'HEXAPOD.Z': 2,
        'HEXAPOD.U': 3, 'HEXAPOD.V': 4, 'HEXAPOD.W': 5}


    scan_settings = {
        'start'         : -1,
        'stop'           : 1,
        'step'          : 0.1,
        'meas_pv_name'  : '18ID:scaler2.S6',
        'scaler_pv_name': '18ID:scaler2',
        'meas_time'     : 0.1,
        'fw_height'     : 0.5,
        'center_offset' : 0,
        'motor'         : np_motor,
        'shutter_pvs'   : [{'name': '18ID:LJT4:2:Bo6', 'open': 0, 'close': 1},
                                {'name': '18ID:LJT4:2:Bo9', 'open': 1, 'close': 0}],
    }


    # Get EPICS PVs
    for shutter in scan_settings['shutter_pvs']:
        pv_name = shutter['name']
        pv = epics.get_pv(pv_name)
        shutter['pv'] = pv

    meas_pv = epics.get_pv(scan_settings['meas_pv_name'])
    count_time = epics.get_pv('{}.TP'.format(scan_settings['scaler_pv_name']))
    count_start = epics.get_pv('{}.CNT'.format(scan_settings['scaler_pv_name']))

    scan_settings['meas_pv'] = meas_pv
    scan_settings['count_time_pv'] = count_time
    scan_settings['count_start_pv'] = count_start

    starting_positions = {}
    last_positions = {}
    settled = {}

    for mname in motors:
        initial_pos = np_motor.get_positioner_position(mname, index_ref[mname])
        print('{} initial position: {}'.format(mname, initial_pos))

        starting_positions[mname] = initial_pos
        last_positions[mname] = initial_pos
        settled[mname] = False

    # Do scan
    try:
        while True:
            for mname in motors:
                scan_settings['scan_positioner'] = mname
                scan_settings['scan_mindex'] = index_ref[mname]

                new_pos = do_centering_scan(scan_settings)

                if np.isclose(new_pos, last_positions[mname], rtol=1e-4):
                    settled[mname] = True
                else:
                    settled[mname] = False

                last_positions[mname] = new_pos

            status = [settled[mname] for mname in motors]
            if all(status):
                break

    except:
        print('Centering interrupted before converging!')
    finally:
        # Clean up
        for mname in motors:
            print('{} moved from {} to {}'.format(mname, starting_positions[mname], last_positions[mname]))
        for shutter in scan_settings['shutter_pvs']:
            close_val = shutter['close']
            pv = shutter['pv']
            pv.put(close_val)

        meas_pv.disconnect()
        count_time.disconnect()
        count_start.disconnect()
