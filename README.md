# Beamline control - users
Beamline control programs and utilities for the BioCAT (Sector 18) beamline at the APS.
This is aimed at user-side control programs.

Contains:
BioCON - General user controls. Currently pump and flow meter controls, but will
be expanded to include exposure controls and the like.

mxmap - A 2D mapping program mostly used at Sector 10 (MR-CAT) right now. It uses
the MX control system to talk to the devices. It scans two motors, then creates a
2D map of measured intensity values from MX scalers.

Requirements:
BioCON: wx, pyserial

mxmap: numpy, Mp, pandas, wx, matplotlib


Installation:
BioCON: Clone the git, then run any of the files in the biocon folder as appropriate.

*   pumpcon.py - run directly, yields a simple GUI for pump control. Also can be imported
    and have the pump control thread used as part of a larger GUI.
*   fmcon.py - run directly, yields a simple GUI for flow meter control. Also can
    be imported and have the flow meter control thread used as part of a larger GUI.

mxmap: Run setup.py as usual.

*   Note: the best way to run mxmap when developing is to be in the outer mxmap folder, and
    use the command: python -m mxmap.main() scan  (or can use read as the last parameter)
