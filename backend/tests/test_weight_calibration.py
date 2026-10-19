"""两点标定增益修正：加载 JSON 系数、异常回退 1.0、读取器注入。"""
import json

from app.weight import WeightScaleReader, _load_gain_factor


def test_load_gain_factor_valid(tmp_path):
    p = tmp_path / "calib.json"
    p.write_text(json.dumps({"gain_factor": 0.937}), encoding="utf-8")
    assert _load_gain_factor(p) == 0.937


def test_load_gain_factor_missing_file(tmp_path):
    assert _load_gain_factor(tmp_path / "nope.json") == 1.0


def test_load_gain_factor_out_of_range_falls_back(tmp_path):
    p = tmp_path / "calib.json"
    p.write_text(json.dumps({"gain_factor": 9.9}), encoding="utf-8")
    assert _load_gain_factor(p) == 1.0  # 超出合理范围（0.5~2.0）不采信


def test_load_gain_factor_garbage_file(tmp_path):
    p = tmp_path / "calib.json"
    p.write_text("not json", encoding="utf-8")
    assert _load_gain_factor(p) == 1.0


def test_reader_accepts_injected_gain():
    r = WeightScaleReader(gain_factor=0.937)
    assert r._gain_factor == 0.937
    assert WeightScaleReader(gain_factor=1.0)._gain_factor == 1.0
