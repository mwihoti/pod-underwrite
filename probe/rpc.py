import json, urllib.request

RPCS = ["https://bsc-dataseed.binance.org","https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com","https://1rpc.io/bnb",
        "https://bsc-dataseed1.defibit.io","https://bsc.drpc.org"]

def rpc(url, method, params, timeout=25):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json","User-Agent":"pod-probe/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def pick():
    for u in RPCS:
        try:
            r = rpc(u, "eth_chainId", [])
            if "result" in r:
                print("OK", u, "chainId", int(r["result"],16))
                return u
        except Exception as e:
            print("FAIL", u, type(e).__name__, e)
    return None

if __name__ == "__main__":
    u = pick()
    print("PICKED", u)
    if u:
        print("block", int(rpc(u,"eth_blockNumber",[])["result"],16))
