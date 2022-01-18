# Beamline control - users
Beamline control programs and utilities for the BioCAT (Sector 18) beamline at the APS.
This is aimed at user-side control programs.


## Contains:

### BioCON

General user controls. Currently pump and flow meter controls, but will
be expanded to include exposure controls and the like.

#### pumpcon.py

Provides pump control. Has a direct object oriented control, which is easy to use
for the command line, a control thread, for integration into a GUI, a simple panel
that can be used as part of a larger GUI, and a frame that is shown when the
file is run directly and provides a simple interface for controlling an arbitrary
number of pumps.

Pumps currently supported:

*   VICI M50 Pump using an MForce Controller over a serial connection


#### fmcon.py

Provides flow meter control. Has a direct object oriented control, which is easy to use
for the command line, a control thread, for integration into a GUI, a simple panel
that can be used as part of a larger GUI, and a frame that is shown when the
file is run directly and provides a simple interface for controlling an arbitrary
number of flow meters.

Flow meters currently supported:

*   Elveflow BFS using the Elveflow SDK (Windows only) over a serial connection

### mxmap

A 2D mapping program mostly used at Sector 10 (MR-CAT) right now. It uses
the MX control system to talk to the devices. It scans two motors, then creates a
2D map of measured intensity values from MX scalers.


## Requirements:

### BioCON

wxpython, pyserial, numpy, six, zaber.serial, pyzmq, matplotlib, future, Mp

Elveflow SDK: https://www.elveflow.com/microfluidic-flow-control-products/flow-control-system/elveflow-software/
Unpack and install to c:\Users\biocat\Elvefow_SDK_Vx_xx_xx

### mxmap

numpy, pandas, wx, matplotlib, Mp


## Installation:

### BioCON

Clone the git, then run any of the files in the biocon folder as appropriate.

Sample commands:

conda install wxpython pyserial numpy six pyzmq matplotlib future

pip install zaber.serial

Update the path to the SDK in fmcon.py appropriately. Update the path to the
Elveflow DLL in the sdk/python_xx/ElveflowXX.py file.

The Elveflow SDK now seems to require that the Labview 2015 runtime be installed.
Install the appropriate version based on if you installed 32bit or 64bit SDK.
http://www.ni.com/en-us/support/downloads/software-products/download.labview.html#329059


### mxmap

Clone the git, then run setup.py as usual.

*   Note: the best way to run mxmap when developing is to be in the outer mxmap folder, and
    use the command: python -m mxmap.main() scan  (or can use read as the last parameter)
