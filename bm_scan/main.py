#!/usr/bin/python
import sys
import bm_scan.gui.scan_gui as scan_gui
import bm_scan.gui.read_gui as read_gui

def main(args=None):
    if args is None:
        args = sys.argv

    run = False
    if len(args) == 2:
        if args[1] == 'scan':
            scan_gui.begin()
            run = True
        elif args[1] == 'read':
            read_gui.begin()
            run = True

    if not run:
        print("Please specify scan or read")



if __name__ == "__main__":
    main(sys.argv)