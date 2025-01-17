import re

import regex

from yimt.api.utils import detect_lang
from yimt.corpus.utils import is_ascii, has_zh, is_ascii_char


class Filter(object):
    """Parallel corpus filter base class"""

    def filter(self, src, tgt):
        """

        Args:
            src: source sentence
            tgt: target sentence
        Returns:
            None if invalid, otherwise pair
        """
        pass


class SameFilter(Filter):
    """Filter pair with same source and target"""

    def __init__(self, lower=True):
        self._lower = lower

    def filter(self, src, tgt):
        if self._lower:
            if src.strip().lower() == tgt.strip().lower():
                return None
        else:
            if src.strip() == tgt.strip():
                return None

        return src, tgt


class HasZhFilter(Filter):

    def __init__(self, filter_src=True):
        self.filter_src = filter_src

    def filter(self, src, tgt):
        if self.filter_src:
            if has_zh(src):
                return None
        else:
            if has_zh(tgt):
                return None

        return src, tgt


class OverlapFilter(Filter):
    """Filter pair whose source and target have too much overlap"""

    def __init__(self, ratio=0.8):
        self._ratio = ratio

    def filter(self, src, tgt):
        import difflib

        s = difflib.SequenceMatcher(None, src, tgt)
        if s.ratio() > self._ratio:
            return None
        return src, tgt


class EmptyFilter(Filter):
    """Filter pair whose source or target is empty"""

    def filter(self, src, tgt):
        if len(src.strip()) == 0 or len(tgt.strip()) == 0:
            return None

        return src, tgt


class AllASCII(Filter):
    """Filter pair whose src and target are english"""

    def filter(self, src, tgt):
        is_src_en = is_ascii(src)
        is_tgt_en = is_ascii(tgt)

        if is_src_en and is_tgt_en:
            return None
        return src, tgt


class ASCIIRatioFilter(Filter):
    """Filter pair whose src and target are english"""

    def __init__(self, threshold=0.67, filter_src=False, filter_tgt=True):
        self._threshold = threshold
        self._filter_src = filter_src
        self._filter_tgt = filter_tgt

    def n_ascii(self, s):
        n = 0
        for c in s:
            if is_ascii_char(c):
                n += 1

        return n

    def filter(self, src, tgt):
        if self._filter_src and self.n_ascii(src)/len(src) > self._threshold:
            return None
        if self._filter_tgt and self.n_ascii(tgt)/len(tgt) > self._threshold:
            return None

        return src, tgt


class LangFilter(Filter):
    """Filter pair with wrong language"""

    def __init__(self, src_lang, tgt_lang):
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang

    def filter(self, src, tgt):
        if detect_lang(src) != self.src_lang or detect_lang(tgt) != self.tgt_lang:
            return None

        return src, tgt


class LenFilter(Filter):
    """Filter pair which is too long or short"""

    def __init__(self, src_lens=(None, None), tgt_lens=(None, None), src_len_fn=len, tgt_len_fn=len):
        self.src_min_len = src_lens[0]
        self.src_max_len = src_lens[1]
        self.tgt_min_len = tgt_lens[0]
        self.tgt_max_len = tgt_lens[1]

        self.src_len_fn = src_len_fn
        self.tgt_len_fn = tgt_len_fn

    def filter(self, src, tgt):
        src_len = self.src_len_fn(src)
        tgt_len = self.tgt_len_fn(tgt)

        if self.src_min_len is not None and src_len < self.src_min_len:
            return None
        if self.src_max_len is not None and src_len > self.src_max_len:
            return None
        if self.tgt_min_len is not None and tgt_len < self.tgt_min_len:
            return None
        if self.tgt_max_len is not None and tgt_len > self.tgt_max_len:
            return None
        return src, tgt


class LenDiffFilter(Filter):
    """Filter pair whose source and target have big length difference"""

    def __init__(self, ratio, src_len_fn=len, tgt_len_fn=len):
        self.ratio = ratio
        self.src_len_fn = src_len_fn
        self.tgt_len_fn = tgt_len_fn

    def filter(self, src, tgt):
        len_src = self.src_len_fn(src)
        len_tgt = self.tgt_len_fn(tgt)

        if len_src <= self.ratio * len_tgt and len_tgt <= self.ratio * len_src:
            return src, tgt
        else:
            return None


class LengthFilter(Filter):

    space_sep_len_f = lambda s: len(s.split())
    char_len_f = lambda s: len(s)

    def __init__(self, src_len_fn=len, tgt_len_fn=len,
                 src_lens=(None, None), tgt_lens=(None, None),
                 ratio=3):
        self.src_min_len = src_lens[0]
        self.src_max_len = src_lens[1]
        self.tgt_min_len = tgt_lens[0]
        self.tgt_max_len = tgt_lens[1]

        self.src_len_fn = src_len_fn
        self.tgt_len_fn = tgt_len_fn

        self.ratio = ratio

    def filter(self, src, tgt):
        src_len = self.src_len_fn(src)
        tgt_len = self.tgt_len_fn(tgt)

        if self.src_min_len is not None and src_len < self.src_min_len:
            return None
        if self.src_max_len is not None and src_len > self.src_max_len:
            return None
        if self.tgt_min_len is not None and tgt_len < self.tgt_min_len:
            return None
        if self.tgt_max_len is not None and tgt_len > self.tgt_max_len:
            return None

        if src_len <= self.ratio * tgt_len and tgt_len <= self.ratio * src_len:
            return src, tgt
        else:
            return None


class LongWordFilter(Filter):
    """Used for languages with space for word divider"""
    def __init__(self, max_long=(40, 40)):
        self.src_max_len = max_long[0]
        self.tgt_max_len = max_long[1]

    def filter(self, src, tgt):
        if self.src_max_len is None and self.tgt_max_len is None:
            return src, tgt

        if self.src_max_len is not None and max([len(w) for w in src.split()]) > self.src_max_len:
            return None

        if self.tgt_max_len is not None and max([len(w) for w in tgt.split()]) > self.tgt_max_len:
            return None

        return src, tgt


class AlphabetRatioFilter(Filter):
    """Proportion of alphabetic characters in the segment"""

    def __init__(self, threshold=0.75, exclude_whitespace=False):
        self.threshold = threshold
        self.exclude_whitespace = exclude_whitespace
        self.re_whitespace = regex.compile(r'\s')
        self.re_not_alphas = regex.compile(r'\p{Alphabetic=No}')

    def filter(self, src, tgt):
        if self.score(src) >= self.threshold and self.score(tgt) >= self.threshold:
            return src, tgt

        return None

    def score(self, s):
        segment = s
        if self.exclude_whitespace:
            segment = self.re_whitespace.sub('', s)
        alphas = self.re_not_alphas.sub('', s)
        r = len(alphas) / len(segment)
        return r


class CharacterRatioFilter(Filter):
    """Proportion of alphabetic characters that are in the given script

    For a list of valid scripts, see e.g.
    https://www.regular-expressions.info/unicode.html

    """
    lang2script = {
        "zh": "Han",
        "en": "Latin",
        "ko": "Hangul",
        "ar": "Arabic",
        "th": "Thai",
    }

    def __init__(self, scripts, thresholds=None):
        self.scripts = scripts
        self.thresholds = [1] * len(scripts) if thresholds is None else thresholds
        self.re_not_alphas = regex.compile(r'\p{Alphabetic=No}')
        self.re_not_script = [regex.compile(fr'\p{{^Script={script}}}')
                              for script in self.scripts]

    def score(self, sent, idx):
        alphas = self.re_not_alphas.sub('', sent)
        if alphas:
            script = self.re_not_script[idx].sub('', alphas)
            return len(script) / len(alphas)
        else:
            return 0.0

    def filter(self, src, tgt):
        if self.score(src, 0) < self.thresholds[0] or self.score(tgt, 1) < self.thresholds[1]:
            return None

        return src, tgt


class AugumentForZhFilter(Filter):

    def __init__(self):
        self.en_word_regex = re.compile(r"[a-zA-Z0-9]+")

    def _get_en_words(self, s):
        en_words = []
        for m in re.finditer(self.en_word_regex, s):
            en_words.append(m.group(0))

        return en_words

    def _exist(self, words, s):
        for w in words:
            if s.find(w) == -1:
                return False

        return True

    def filter(self, src, tgt):
        src_en_words = self._get_en_words(src)
        if not self._exist(src_en_words, tgt):
            return None

        tgt_en_words = self._get_en_words(tgt)
        if not self._exist(tgt_en_words, src):
            return None

        return src, tgt

