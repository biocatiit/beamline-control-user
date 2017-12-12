#! /usr/bin/env python
#
#  Name:    example1.py
#
#  Purpose: This example performs a simple step scan of a motor
#           by creating and then executing an MX scan record.
#
#  Author:  William Lavender
#
#-------------------------------------------------------------------------
#
#  Copyright 2001 Illinois Institute of Technology
#
#  See the file "LICENSE" for information on usage and redistribution
#  of this file, and for a DISCLAIMER OF ALL WARRANTIES.
#
#-------------------------------------------------------------------------
#

import sys

sys.stdout = sys.stderr

#  Define the location of the MX installation directory.

mxdir = "/opt/mx"

#  Define the location of the example MX database.

database_filename = "db_soft.dat"

#  Construct the name of the Mp package directory.

###mp_package_dir = [ "/home/lavender/mxdev/0.56.0/mp/libMp" ]

mp_package_dir = [ mxdir + "lib/mp" ]

#  Add the Mp package directory to the module load path.

sys.path[:0] = mp_package_dir

#  Load the Mp package.

import Mp

#  Read in the MX database used by this example.

record_list = Mp.setup_database( database_filename )

#  Find the devices to be used by the scan.

energy_motor_record = record_list.get_record("energy")

i_zero_record = record_list.get_record("Io")

# i_trans_record = record_list.get_record("It")

timer_record = record_list.get_record("timer1")

#  Setup the scan parameters for a copper K edge scan.

start = 8950.0		# in eV
step_size = 1.0		# in eV
num_steps = 101
measurement_time = 1.0	# in seconds
settling_time = 0.1     # in seconds

#  Construct a scan description for the requested scan.

fmt = "example2scan scan linear_scan motor_scan \"\" \"\" "

# fmt = fmt + "1 1 1 energy 1 I1 0 %g preset_time \"%g timer1\" "
#
# fmt = fmt + "text example2scan.out none "
#
# fmt = fmt + "log($f[0]/$f[1]) %g %g %d"
#
# description = fmt % \
# 	( settling_time, measurement_time, start, step_size, num_steps )

fmt = fmt + "1 2 2 x1 x2 1 I1 0 %g preset_time \"%g timer1\" "

fmt = fmt + "text example2scan.out none "

fmt = fmt + "log($f[0]/$f[1]) 1 0 1 1 3 4"

description = fmt % \
	( settling_time, measurement_time)

print "Scan description = '%s'\n" % ( description )

#  Add a corresponding scan record to the MX database of the current process.

scan_record = record_list.create_record_from_description( description )

scan_record.finish_record_initialization()

#  Perform the scan.

scan_record.perform_scan()

#  Delete the scan record from the client's database.
#
#  Strictly speaking, this is not really necessary since the example
#  program is about to exit anyway.  It is shown primarily to give
#  an example of how scan records are normally deleted in long running
#  application programs.

scan_record.delete_record()

