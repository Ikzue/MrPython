
def f(L):
    """ list[tuple[str, int]] -> int """
    # s : str
    # k : int
    s,k = L[0]
    for u,v in L[:]:
        if v > k:
            k = v
    return k



