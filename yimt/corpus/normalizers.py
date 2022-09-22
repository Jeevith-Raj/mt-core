from yimt.corpus.utils import is_ascii_char, hant_2_hans


class Normalizer(object):

    def normalize(self, s):
        """Normalize text

        Args:
            s: string
        Returns:
            the normalize string
        """
        pass


class SpaceNormalizer(Normalizer):
    """Remove unnecessary spaces"""

    def normalize(self, s):
        import re

        s = s.strip()
        s = s.replace("\u3000", " ")
        s = s.replace("\xa0", " ")
        s = re.sub(r"\s{2,}", " ", s)
        s = s.strip()

        new_s = ""
        for i in range(len(s)):
            if s[i] == " " and (
                    (i > 0 and not is_ascii_char(s[i - 1]))
                    or (i < len(s) - 1 and not is_ascii_char(s[i + 1]))):
                continue
            else:
                new_s += s[i]
        return new_s


def not_print_en(s):
    return is_ascii_char(s) and not ('\u0020' <= s[0] <= '\u007e' or s[0] == '\u0009')


class NoPrintNormalizer(Normalizer):
    """Remove the characters that cannot be printed"""

    def normalize(self, s):
        new_s = ""

        for c in s:
            if ('\u0000' <= c <= '\u0008') or ('\u000a' <= c <= '\u001f') or c == '\u007f':
                continue
            new_s += c
        return new_s


punct_pairs = [('“', '”'), ('"', '"'), ("‘", "’"), ("（", "）"), ("《", "》"), ("(", ")")]


def normalize_pair_punct(s, left_punct, right_punct):
    s = s.replace(left_punct + left_punct, "")
    s = s.replace(left_punct + left_punct + left_punct, left_punct)

    s = s.replace(right_punct + right_punct, "")
    s = s.replace(right_punct + right_punct + right_punct, right_punct)

    idx = s.find(left_punct)
    if idx >= 0:
        has_pair = s.find(right_punct, idx + 1)
        if has_pair == -1:
            s = s[:idx] + s[idx + 1:]
            # s = s.replace(left_punct, "")

    idx = s.find(right_punct)
    if idx >= 0:
        has_pair = s.find(left_punct, 0, idx)
        if has_pair == -1:
            s = s[:idx] + s[idx + 1:]
            # s = s.replace(right_punct, "")

    return s


class PairPunctNormalizer(Normalizer):

    def normalize_pair(self, src, tgt):
        for p in punct_pairs:
            src = normalize_pair_punct(src, p[0], p[1])
            tgt = normalize_pair_punct(tgt, p[0], p[1])

        return src, tgt

    def normalize(self, s):
        pair = s.split("\t")
        src = pair[0]
        tgt = pair[1]

        src, tgt = self.normalize_pair(src, tgt)

        return src + "\t" + tgt


class Hant2Hans(Normalizer):
    """Traditional Chinese to Simplified Chinese"""

    def __init__(self, norm_src=True, norm_tgt=True):
        self.norm_src = norm_src
        self.norm_tgt = norm_tgt

    def normalize(self, s):
        if not self.norm_src and not self.norm_tgt:
            return s

        pair = s.split("\t")
        if len(pair) != 2:
            print(s)
            return s
        src = pair[0]
        tgt = pair[1]
        if self.norm_src:
            src = hant_2_hans(src)

        if self.norm_tgt:
            tgt = hant_2_hans(tgt)

        return src + "\t" + tgt