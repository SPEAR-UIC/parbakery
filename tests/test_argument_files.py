"""parbake/tests/test_argument_files.py - tests for flags read from an @ file (arguments.py)

Run: pytest tests/test_argument_files.py

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

from arguments import make_parser as make_argument_parser
from arguments import read_argument_file_line


def make_parser():
    """Return a parser with a directory, --out, --workers and --full."""
    parser = make_argument_parser("test")
    parser.add_argument("directory", nargs="?", default=".")
    parser.add_argument("--out")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--full", action="store_true")
    return parser


def test_a_line_splits_like_a_shell():
    assert read_argument_file_line("--out reports/") == ["--out", "reports/"]
    assert read_argument_file_line('--out "my reports"') == ["--out", "my reports"]
    assert read_argument_file_line("--full   # read everything") == ["--full"]


def test_comments_and_blank_lines_give_nothing():
    assert read_argument_file_line("# just a note") == []
    assert read_argument_file_line("") == []
    assert read_argument_file_line("   ") == []


def test_flags_are_read_from_the_file(tmp_path):
    argument_file = tmp_path / "run.args"
    argument_file.write_text(
        "# a practice run\n"
        "/data/somewhere\n"
        "\n"
        "--out reports/\n"
        "--workers=4\n"
        "--full\n"
    )
    arguments = make_parser().parse_args([f"@{argument_file}"])
    assert arguments.directory == "/data/somewhere"
    assert arguments.out == "reports/"
    assert arguments.workers == 4
    assert arguments.full


def test_a_flag_after_the_file_overrides_it(tmp_path):
    argument_file = tmp_path / "run.args"
    argument_file.write_text("--workers 4\n")
    arguments = make_parser().parse_args([f"@{argument_file}", "--workers", "8"])
    assert arguments.workers == 8
