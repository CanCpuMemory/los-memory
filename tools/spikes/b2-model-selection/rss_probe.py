import json, os, resource, subprocess, sys, time
import numpy as np, onnxruntime as ort
from tokenizers import Tokenizer

def rss():
    out = subprocess.run(["ps","-o","rss=","-p",str(os.getpid())], capture_output=True, text=True).stdout.strip()
    return int(out)//1024 if out else -1

def run(tuned: bool):
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    if tuned:
        so.enable_cpu_mem_arena = False
        so.enable_mem_pattern = False
    s = ort.InferenceSession("e5_onnx_model_quantized.onnx", so, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file("e5_tokenizer.json"); tok.enable_truncation(max_length=256)
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    texts = ["passage: 有些功能明明做完了却像没接上是什么问题 %d" % i for i in range(256)]
    t0=time.time()
    for start in range(0, len(texts), 32):
        enc = tok.encode_batch(texts[start:start+32])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in {i.name for i in s.get_inputs()}:
            feed["token_type_ids"] = np.zeros_like(ids)
        s.run(None, feed)
    return {"tuned": tuned, "steady_rss_mib": rss(),
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024,
            "ms_per_32batch": round((time.time()-t0)*1000/8, 1)}

print(json.dumps([run(False), run(True)], indent=2))
