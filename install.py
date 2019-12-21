# $Id: install.py 1559 2016-09-28 19:59:02Z mwall $
# installer for as3935 lightning detection on raspberry pi
# Copyright 2015 Matthew Wall

from setup import ExtensionInstaller

def loader():
    return AS3935Installer()

class AS3935Installer(ExtensionInstaller):
    def __init__(self):
        super(AS3935Installer, self).__init__(
            version="0.6",
            name='as3935',
            description='Capture lightning data from AS3935 hardware',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            process_services='user.as3935.AS3935',
            config={
                'AS3935': {
                    'address': '3',
                    'bus': '1',
                    'pin': '17',
                    'calibration': '6'}},
            files=[('bin/user', ['bin/user/as3935.py'])]
            )
