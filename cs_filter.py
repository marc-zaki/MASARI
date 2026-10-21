import re

# Comprehensive list of Computer Science and Programming keywords
CS_KEYWORDS = [
    r"\bpython\b", r"\bprogramming\b", r"\bcode\b", r"\bcoding\b", r"\bsoftware\b",
    r"\bdeveloper\b", r"\bjava\b", r"\bjavascript\b", r"\bc\+\+\b", r"\bc#\b", r"\bhtml\b",
    r"\bcss\b", r"\breact\b", r"\bnode\b", r"\bweb dev\b", r"\bweb development\b",
    r"\bmachine learning\b", r"\bartificial intelligence\b", r"\bdata science\b",
    r"\bdatabase\b", r"\bsql\b", r"\bcybersecurity\b", r"\bcyber security\b",
    r"\bnetwork\b", r"\balgorithm\b", r"\bdata structures\b", r"\bcloud\b", r"\baws\b",
    r"\bgit\b", r"\blinux\b", r"\bdevops\b", r"\bdocker\b", r"\bkubernetes\b",
    r"\bflutter\b", r"\bandroid\b", r"\bios\b", r"\bswift\b", r"\bkotlin\b",
    r"\btypescript\b", r"\bruby\b", r"\bphp\b", r"\bgo\blang\b", r"\bgolang\b",
    r"\brust\b", r"\bcompiler\b", r"\boperating system\b", r"\bcomputer science\b",
    r"\bfront\b-?end\b", r"\bback\b-?end\b", r"\bfull\b-?stack\b", r"\bdeep learning\b",
    r"\bneural network\b", r"\bdata analytics\b", r"\bux/ui\b", r"\bagile\b"
]

def is_cs_related(title, description):
    """
    Checks if a course is CS/programming related by scanning title and description.
    """
    text_to_scan = f"{title} {description}".lower()
    for kw in CS_KEYWORDS:
        if re.search(kw, text_to_scan):
            return True
    return False
