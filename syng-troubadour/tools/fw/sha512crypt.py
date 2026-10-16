#!/usr/bin/env python3
"""Pure-Python SHA-512 crypt ($6$) for /etc/shadow, for hosts without Python's crypt module (macOS 3.13+).
Usage: sha512crypt.py <password> [salt]   -> prints the $6$salt$hash string."""
import hashlib, os, sys, base64

B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

def _b64_3(b2, b1, b0, n):
    w = (b2 << 16) | (b1 << 8) | b0; out = ""
    for _ in range(n):
        out += B64[w & 0x3f]; w >>= 6
    return out

def sha512_crypt(password, salt=None, rounds=5000):
    pw = password.encode(); salt = (salt or base64.b64encode(os.urandom(12)).decode()[:16].replace("+", ".")).encode()[:16]
    B = hashlib.sha512(pw + salt + pw).digest()
    a = hashlib.sha512(); a.update(pw + salt)
    n = len(pw)
    while n > 64: a.update(B); n -= 64
    a.update(B[:n])
    n = len(pw)
    while n > 0:
        a.update(B if n & 1 else pw); n >>= 1
    A = a.digest()
    DP = hashlib.sha512(pw * len(pw)).digest()
    P = (DP * (len(pw) // 64 + 1))[:len(pw)]
    DS = hashlib.sha512(salt * (16 + A[0])).digest()
    S = (DS * (len(salt) // 64 + 1))[:len(salt)]
    C = A
    for i in range(rounds):
        c = hashlib.sha512()
        c.update(P if i & 1 else C)
        if i % 3: c.update(S)
        if i % 7: c.update(P)
        c.update(C if i & 1 else P)
        C = c.digest()
    order = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29),
             (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35), (15, 36, 57), (37, 58, 16),
             (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41)]
    out = "".join(_b64_3(C[i], C[j], C[k], 4) for i, j, k in order) + _b64_3(0, 0, C[63], 2)
    return "$6$%s$%s" % (salt.decode(), out)

if __name__ == "__main__":
    print(sha512_crypt(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
