# 外部 API 自动测试

`POST /api/v1/test` 同步执行现有 API 自动测试和统一指纹归因，无需先获取挑战或逐次提交回答。此接口由 Python 后端提供，GitHub Pages 静态站点不提供该接口。

## 调用

按项目说明安装依赖并运行 `python start.py` 后：

```sh
curl --fail-with-body http://127.0.0.1:7860/api/v1/test \
  -H 'Content-Type: application/json' \
  --data '{
    "baseurl": "https://your-provider.example/v1",
    "api_model": "your-provider-model-name",
    "api_key": "your-provider-api-key",
    "temperature": 0.7
  }'
```

| 请求字段 | 必填 | 含义 |
| --- | --- | --- |
| `baseurl` | 是 | 上游 HTTP(S) API 地址；也接受现有字段名 `base_url`，同时提供时以 `baseurl` 为准。不能带 URL 凭据、查询参数或片段。 |
| `api_model` | 是 | 发给上游的模型名，可以是渠道模型别名。 |
| `api_key` | 是 | 上游 API Key，非空字符串。只用于本次调用，不落盘。 |
| `temperature` | 否 | 0–2 的有限 JSON 数字；省略或 `null` 时不向上游发送温度，使用上游默认值。上游自身可能有更严格的限制。 |

自动尝试 OpenAI Chat Completions 和 Anthropic Messages 格式；不需要传协议参数。根地址、`/v1` 和相应协议的完整端点均沿用原有 URL 拼接规则。

## 返回

成功响应为 HTTP 200，顶层只有两个字段：

- `model_name`：统一候选库中概率最高的**具体模型显示名**，不是传入的上游别名，也不是模型家族名。
- `addtional_data`：完整的原自动测试结果。此字段按需求保留 `addtional_data` 拼写。

`addtional_data` 包含：

| 字段 | 含义 |
| --- | --- |
| `prediction` / `prediction_name` / `probability` | 获胜模型 ID、显示名与概率（0–1） |
| `results` | 按概率降序排列的全部候选、分数、相似度与家族信息 |
| `family_prediction` / `family_prediction_name` / `family_probability` / `family_probabilities` | 家族归因与分布 |
| `used_outputs` / `diagnostics` | 有效回答数及每份回答的数字数量、采纳情况 |
| `calibration` / `method` | 校准参数及归因方法 |
| `api_test` | `requested`、`attempted`、`max_attempts`、`received`、`errors` |
| `bank` | 当前指纹库摘要 |

读取结果示例：

```python
import json
from urllib.request import Request, urlopen

payload = {
    "baseurl": "https://your-provider.example/v1",
    "api_model": "your-provider-model-name",
    "api_key": "your-provider-api-key",
}
request = Request(
    "http://127.0.0.1:7860/api/v1/test",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
with urlopen(request, timeout=3600) as response:
    result = json.load(response)
print(result["model_name"])
print(result["addtional_data"]["probability"])
```

最多进行 6 次挑战，以收集 3 份有效回答为目标；有 1–2 份有效回答也会返回归因，应检查 `api_test.received`。概率是现有候选库内的闭集归因概率，不能证明未收录模型的真实身份。

错误响应均为 `{"error": "错误说明"}`：

| 状态码 | 含义 |
| --- | --- |
| 400 | 无效 JSON、缺失/错误类型的必填参数、无效 URL 或温度 |
| 502 | 上游调用失败或所有回答均不足以归因 |
| 503 | 没有可用指纹库 |

沿用上游每次 HTTP 调用 240 秒超时、可重试错误最多 3 次的策略；协议探测和多个挑战可能使总耗时较长。调用方和反向代理需设置足够的响应超时，示例的 3600 秒不是服务端总超时上限。每次测试会消耗上游模型额度。

服务默认仅监听本机。跨机器调用应部署在可信网络或配置带身份认证、TLS 和访问限制的反向代理；此接口接受调用者指定的上游地址和密钥，应仅开放给可信调用者。不要在代理日志中记录请求体。已有 `/api/test/auto` 及网页接口保持原响应格式。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试用本地 HTTP 上游重放仓库已有参考样本，经过真实采样、协议解析和归因流程，无需真实 API Key，也不会消耗模型额度。
