# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest

import support  # noqa: F401

from core import instance


class InstanceId(unittest.TestCase):
    def test_stable_across_restarts(self):
        folder = tempfile.mkdtemp()
        first = instance.lease_instance_id(folder)
        first.release()
        again = instance.lease_instance_id(folder)
        again.release()
        self.assertEqual(first.instance_id, again.instance_id)
        with open(os.path.join(folder, "instance_id")) as handle:
            self.assertEqual(handle.read().strip(), first.instance_id)

    def test_second_blender_gets_its_own_slot(self):
        folder = tempfile.mkdtemp()
        a = instance.lease_instance_id(folder)
        b = instance.lease_instance_id(folder)
        c = instance.lease_instance_id(folder)
        self.assertEqual(b.instance_id, a.instance_id + "-2")
        self.assertEqual(c.instance_id, a.instance_id + "-3")
        b.release()
        d = instance.lease_instance_id(folder)
        self.assertEqual(d.instance_id, a.instance_id + "-2")
        for lease in (a, c, d):
            lease.release()

    def test_override(self):
        self.assertEqual(instance.lease_instance_id("/nonexistent", override="farm-7").instance_id, "farm-7")

    def test_no_locks_on_this_drive_keeps_the_plain_id(self):
        folder = tempfile.mkdtemp()
        original = instance._try_lock
        instance._try_lock = lambda path: (None, instance.UNSUPPORTED)
        try:
            lease = instance.lease_instance_id(folder)
        finally:
            instance._try_lock = original
        with open(os.path.join(folder, "instance_id")) as handle:
            self.assertEqual(lease.instance_id, handle.read().strip())


if __name__ == "__main__":
    unittest.main()
