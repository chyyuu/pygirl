"""
Tests for the md5 module implemented at interp-level in pypy/module/md5.
"""

import py
from pypy.conftest import gettestobjspace


class AppTestMD5(object):

    def setup_class(cls):
        """
        Create a space with the md5 module and import it for use by the
        tests.
        """
        cls.space = gettestobjspace(usemodules=['md5'])
        cls.w_md5 = cls.space.appexec([], """():
            import md5
            return md5
        """)


    def test_digest_size(self):
        """
        md5.digest_size should be 16.
        """
        assert self.md5.digest_size == 16


    def test_MD5Type(self):
        """
        Test the two ways to construct an md5 object.
        """
        md5 = self.md5
        d = md5.md5()
        assert isinstance(d, md5.MD5Type)
        d = md5.new()
        assert isinstance(d, md5.MD5Type)


    def test_md5object(self):
        """
        Feed example strings into a md5 object and check the digest and
        hexdigest.
        """
        md5 = self.md5
        cases = (
          ("",
           "d41d8cd98f00b204e9800998ecf8427e"),
          ("a",
           "0cc175b9c0f1b6a831c399e269772661"),
          ("abc",
           "900150983cd24fb0d6963f7d28e17f72"),
          ("message digest",
           "f96b697d7cb7938d525a2f31aaf161d0"),
          ("abcdefghijklmnopqrstuvwxyz",
           "c3fcd3d76192e4007dfb496cca67e13b"),
          ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
           "d174ab98d277d9f5a5611c2c9f419d9f"),
          ("1234567890"*8,
           "57edf4a22be3c955ac49da2e2107b67a"),
        )
        for input, expected in cases:
            d = md5.new(input)
            assert d.hexdigest() == expected
            assert d.digest() == expected.decode('hex')


    def test_copy(self):
        """
        Test the copy() method.
        """
        md5 = self.md5
        d1 = md5.md5()
        d1.update("abcde")
        d2 = d1.copy()
        d2.update("fgh")
        d1.update("jkl")
        assert d1.hexdigest() == 'e570e7110ecef72fcb772a9c05d03373'
        assert d2.hexdigest() == 'e8dc4081b13434b45189a720b77b6818'
