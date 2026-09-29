# SPDX-License-Identifier: GPL-3.0-or-later
"""Handing commands to the main thread, and asking the person first."""

import threading
import unittest

import support  # noqa: F401

from core.commands import Command
from core.mainthread import ApprovalRequest, MainThreadExecutor


def command(kind="run", cid="c1", timeout=60):
    return Command({"id": cid, "kind": kind, "args": {"code": "x = 1"}, "timeout_seconds": timeout},
                   received_at=0.0)


class Harness:
    def __init__(self, ask=True, can_ask=True):
        self.now = 0.0
        self.ran = []
        self.statuses = []
        self.ask = ask
        self.executor = MainThreadExecutor(
            run=self.run,
            needs_approval=self.needs_approval,
            can_ask=lambda: can_ask,
            on_status=lambda cid, status, detail="": self.statuses.append((cid, status)),
            clock=lambda: self.now,
        )

    def run(self, ticket):
        self.ran.append((ticket.command.id, threading.get_ident()))
        return True, {"ran": ticket.command.id}, None

    def needs_approval(self, ticket):
        if self.ask and ticket.command.kind == "run":
            return ApprovalRequest("Your agent wants to run Python in Blender", ["x = 1"], "nobody to ask")
        return None


class Executor(unittest.TestCase):
    def test_runs_on_the_pumping_thread(self):
        h = Harness(ask=False)
        tickets = []
        submitter = threading.Thread(target=lambda: tickets.append(h.executor.submit(command("info"))))
        submitter.start()
        submitter.join()
        self.assertEqual(h.ran, [])  # nothing runs until the main thread pumps
        h.executor.pump()
        self.assertEqual(h.ran, [("c1", threading.get_ident())])
        self.assertTrue(tickets[0].wait(0))
        self.assertEqual((tickets[0].ok, tickets[0].result), (True, {"ran": "c1"}))
        self.assertEqual(h.statuses, [("c1", "running")])

    def test_approve(self):
        h = Harness()
        ticket = h.executor.submit(command())
        h.executor.pump()
        self.assertFalse(ticket.done)
        self.assertEqual([p.id for p in h.executor.approvals], ["c1"])
        self.assertEqual(h.statuses, [("c1", "approval")])
        self.assertTrue(h.executor.approve("c1"))
        h.executor.pump()
        self.assertTrue(ticket.ok)
        self.assertEqual(h.executor.approvals, [])

    def test_deny(self):
        h = Harness()
        ticket = h.executor.submit(command())
        h.executor.pump()
        h.executor.deny("c1")
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("clicked Deny", ticket.error)
        self.assertEqual(h.ran, [])

    def test_nobody_answers_in_time(self):
        h = Harness()
        ticket = h.executor.submit(command(timeout=30))
        h.executor.pump()
        h.now = 26.0
        h.executor.pump()
        self.assertFalse(ticket.done)
        h.now = 27.0  # 3 s before expiry, so the refusal still reaches the API
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("in time", ticket.error)

    def test_headless_cannot_ask(self):
        h = Harness(can_ask=False)
        ticket = h.executor.submit(command())
        h.executor.pump()
        self.assertEqual((ticket.ok, ticket.error), (False, "nobody to ask"))

    def test_switch_off_fails_queued_and_waiting(self):
        h = Harness()
        waiting = h.executor.submit(command(cid="w"))
        h.executor.pump()
        queued = h.executor.submit(command("info", cid="q"))
        h.executor.close("switched off")
        h.executor.pump()
        self.assertEqual((waiting.ok, waiting.error), (False, "switched off"))
        self.assertEqual((queued.ok, queued.error), (False, "switched off"))
        h.executor.reopen()
        again = h.executor.submit(command("info", cid="a"))
        h.executor.pump()
        self.assertTrue(again.ok)

    def test_a_crashing_runner_still_answers(self):
        h = Harness(ask=False)
        h.executor._run = lambda ticket: 1 / 0
        ticket = h.executor.submit(command("info"))
        h.executor.pump()
        self.assertFalse(ticket.ok)
        self.assertIn("division by zero", ticket.error)


if __name__ == "__main__":
    unittest.main()
