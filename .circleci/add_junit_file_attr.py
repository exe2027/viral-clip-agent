"""Add a `file` attribute to every <testcase> in a pytest JUnit XML file.

pytest's junitxml (xunit1 and xunit2 in pytest 9) does not emit `file`
on testcase elements, but CircleCI Smarter Testing needs it to map each
result back to the test atom (file) it ran. Derive it from the classname:
the module part (everything before the last dot) maps to a file path,
e.g. classname "test_accounts.TestComputeState" -> "test_accounts.py",
"pkg.test_x.TestY" -> "pkg/test_x.py". Missing/invalid input is left
unchanged; the file is only rewritten when at least one testcase was
annotated. Usage: python3 .circleci/add_junit_file_attr.py <junit.xml>
"""
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: add_junit_file_attr.py <junit.xml>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    try:
        tree = ET.parse(path)
    except FileNotFoundError:
        print(f"no junit file at {path}, nothing to annotate", file=sys.stderr)
        return 1
    root = tree.getroot()
    annotated = 0
    for case in root.iter("testcase"):
        if case.get("file"):
            continue
        classname = case.get("classname") or ""
        module = classname.rsplit(".", 1)[0] if "." in classname else classname
        if not module:
            continue
        case.set("file", module.replace(".", "/") + ".py")
        annotated += 1
    if annotated:
        tree.write(path, encoding="utf-8", xml_declaration=True)
    print(f"annotated {annotated} testcase(s) with file= in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
