import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from core.rulebook import Rulebook  # noqa: E402

RB = Rulebook.load(HERE.parent / "rulebook" / "rulebook.yml")
CWD = "/Users/jason/code/frontdesk"


def tier(tool, **ti):
    return RB.decide(tool, ti, CWD)


class RulebookTest(unittest.TestCase):
    def test_tier0_never(self):
        self.assertEqual(tier("Bash", command="rm -rf /").tier, 0)
        self.assertEqual(tier("Bash", command="sudo rm -fr /mnt/tank").tier, 0)
        self.assertEqual(tier("Bash", command="zfs destroy tank/video").tier, 0)
        self.assertEqual(tier("Bash", command="git push --force origin main").tier, 0)

    def test_tier1_allow(self):
        self.assertEqual(tier("Read", file_path=f"{CWD}/src/app.ts").tier, 1)
        self.assertEqual(tier("Edit", file_path=f"{CWD}/src/app.ts", old_string="a", new_string="b").tier, 1)
        self.assertEqual(tier("Edit", file_path="src/app.ts").tier, 1)                 # relative to cwd
        self.assertEqual(tier("Bash", command="git status && git diff").tier, 1)
        self.assertEqual(tier("Bash", command="npm test").tier, 1)
        self.assertEqual(tier("Bash", command="ffprobe -v error /mnt/tank/video/h264/a.mp4").tier, 1)
        self.assertEqual(tier("Bash", command="git push -u origin claude/bossk").tier, 1)
        self.assertEqual(tier("mcp__github__list_pull_requests", owner="x", repo="y").tier, 1)

    def test_tier3_ask(self):
        self.assertEqual(tier("Bash", command="rm -rf /mnt/tank/video/originals/shoot1").tier, 3)
        self.assertEqual(tier("Bash", command="git push origin main").tier, 3)
        self.assertEqual(tier("Bash", command="git push").tier, 3)
        self.assertEqual(tier("mcp__Gmail__send_message", to="client@x.com").tier, 3)
        self.assertTrue(tier("mcp__Gmail__send_message", to="client@x.com").confirm)
        self.assertEqual(tier("mcp__Higgsfield__generate_video", prompt="x").tier, 3)
        self.assertEqual(tier("Edit", file_path="/mnt/tank/clients/acme/final.mov").tier, 3)
        self.assertEqual(tier("Read", file_path="/Users/jason/.ssh/id_ed25519").tier, 3)
        self.assertEqual(tier("Bash", command="export STRIPE_SECRET=sk_live_1").tier, 3)

    def test_unless_clause(self):
        # deleting inside the holding folder or build dirs is not tier 3
        d = tier("Bash", command="rm -rf /mnt/tank/video/_holding/a/old.mov")
        self.assertNotEqual(d.tier, 3)
        d = tier("Bash", command=f"rm -rf {CWD}/node_modules")
        self.assertNotEqual(d.tier, 3)
        # but a delete that mixes a holding path with an outside path is still tier 3
        self.assertEqual(tier("Bash", command="rm -rf /mnt/tank/video/_holding/a /mnt/tank/video/originals/b").tier, 3)

    def test_tier2_fallthrough(self):
        self.assertEqual(tier("Bash", command="pip install some-new-package").tier, 2)
        self.assertEqual(tier("Write", file_path="/etc/hosts").tier, 2)
        self.assertEqual(tier("Bash", command="curl -X POST https://api.example.com/x").tier, 2)

    def test_restrictive_wins_over_allow(self):
        # 'rm' inside cwd is still a delete outside approved folders -> 3, even though cwd writes are tier 1
        self.assertEqual(tier("Bash", command=f"rm {CWD}/src/app.ts").tier, 3)
        # reading a secret path is tier 3 even though Read is tier 1
        self.assertEqual(tier("Read", file_path="/Users/jason/.aws/credentials").tier, 3)

    def test_windows_paths(self):
        self.assertEqual(tier("Edit", file_path="Z:\\clients\\acme\\cut.prproj").tier, 3)

    def test_replay(self):
        old = RB
        new = Rulebook.from_dict({"rules": [
            {"id": "allow-pip", "tier": 1, "match": {"tool": "Bash", "command": "^pip install"}},
            {"id": "ask-reads", "tier": 3, "match": {"tool": "Read"}},
        ]})
        prompts = [
            {"ts": 1, "data": {"tool_name": "Bash", "tool_input": {"command": "pip install x"}, "cwd": CWD}},
            {"ts": 2, "data": {"tool_name": "Read", "tool_input": {"file_path": f"{CWD}/a"}, "cwd": CWD}},
            {"ts": 3, "data": {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": CWD}},
        ]
        r = new.replay(prompts, old)
        self.assertEqual(len(r["newly_allowed"]), 1)
        self.assertEqual(r["newly_allowed"][0]["to"], 1)
        self.assertEqual(len(r["newly_restricted"]), 2)  # Read 1->3, git status 1->2


if __name__ == "__main__":
    unittest.main()
