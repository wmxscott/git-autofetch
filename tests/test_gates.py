import pytest

from git_autofetch.gates import parse_idle_seconds, parse_usb_id, usb_device_present

IOREG_USB = """\
+-o Root  <class IORegistryEntry, id 0x100000100, retain 12>
  +-o AppleT8112USBXHCI@00000000  <class AppleT8112USBXHCI, id 0x100000331>
  | {
  |   "IOClass" = "AppleT8112USBXHCI"
  | }
  | +-o Keyboard@00100000  <class IOUSBHostDevice, id 0x100000abc>
  | |   {
  | |     "idProduct" = 834
  | |     "idVendor" = 1452
  | |   }
  | +-o YubiKey OTP+FIDO+CCID@00200000  <class IOUSBHostDevice, id 0x100000def>
  |     {
  |       "idProduct" = 1031
  |       "idVendor" = 4176
  |     }
"""


def test_parse_usb_id():
    assert parse_usb_id("0x1050") == (4176, None)
    assert parse_usb_id("0x1050:0x0407") == (4176, 1031)
    assert parse_usb_id("1050:") == (4176, None)


@pytest.mark.parametrize("spec", ["", "yubikey", "0x1050:0x1:0x2", ":0x407"])
def test_parse_usb_id_rejects(spec):
    with pytest.raises(ValueError):
        parse_usb_id(spec)


def test_vendor_present():
    assert usb_device_present(IOREG_USB, 4176)


def test_vendor_and_product_present():
    assert usb_device_present(IOREG_USB, 4176, 1031)


def test_vendor_absent():
    assert not usb_device_present(IOREG_USB, 1234)


def test_product_must_belong_to_the_same_device():
    # The keyboard's vendor id must not pair with the YubiKey's product id.
    assert usb_device_present(IOREG_USB, 1452, 834)
    assert not usb_device_present(IOREG_USB, 1452, 1031)


def test_vendor_id_is_matched_whole():
    assert not usb_device_present(IOREG_USB, 417)


def test_idle_seconds():
    output = '  | |   "HIDIdleTime" = 65000000000\n'
    assert parse_idle_seconds(output) == 65.0
    assert parse_idle_seconds("nothing here") is None
