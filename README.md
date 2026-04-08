# README

This application is a Rails prompt queue UI and API. The Rails app runs on the
Lightsail instance, and the model inference worker talks to LM Studio over
Tailscale.

## Lightsail + Tailscale deployment

1. Prepare the Lightsail instance with Docker, SQLite, and Tailscale.
2. Join the same Tailscale network on both the Lightsail instance and this PC.
3. Start LM Studio on this PC and expose the local server on the Tailscale
   interface.
4. Run the Rails app on Lightsail.
5. Run the worker on Lightsail or another machine that can reach both the Rails
   app and LM Studio through Tailscale.

## Rails app

Build and run the container on Lightsail:

```bash
docker build -t ruby_on_rails_we_btest .
docker run -d --name ruby_on_rails_we_btest -p 3000:80 \
  -e RAILS_MASTER_KEY=your-master-key \
  -e SECRET_KEY_BASE=your-secret-key \
  -e WORKER_SHARED_TOKEN=replace-with-long-random-token \
  ruby_on_rails_we_btest
```

If you use a plain host deployment instead of Docker, keep the same environment
variables and point Puma at port 3000.

## Tailscale and LM Studio

Use the Tailscale IP of this PC as the LM Studio base URL. In this workspace it
is currently:

```text
http://100.126.42.42:1234/v1
```

Confirm from the Lightsail instance that the endpoint is reachable:

```bash
curl http://100.126.42.42:1234/v1/models
```

If that request fails, check that LM Studio is listening on the Tailscale
interface and that both machines are logged into the same tailnet.

## Worker

The worker claims jobs from Rails, streams tokens from LM Studio, and forwards
chunks back to Rails so the UI can update progressively.

Environment variables:

- `RAILS_API_BASE`: Rails URL reachable from the worker, for example
  `http://100.x.x.x:3000`
- `WORKER_SHARED_TOKEN`: shared token required by `/api/jobs/*`
- `LMSTUDIO_BASE_URL`: LM Studio OpenAI-compatible base URL, for example
  `http://100.126.42.42:1234/v1`
- `LMSTUDIO_MODEL`: optional model name; if omitted, the worker tries to pick
  the first model returned by LM Studio

Run the worker with:

```bash
python3 script/lmstudio_worker.py
```

## AI Prompt Queue API (Rails <-> Python Worker)

Rails側を正本として、プロンプトと回答を `prompt_requests` テーブルで管理します。
Pythonワーカーは長ポーリングでジョブを取得し、結果を返却します。

### 1. DB migration

```bash
bin/rails db:migrate
```

### 2. Worker token

ワーカー用の共有トークンを環境変数で設定します。

```bash
set WORKER_SHARED_TOKEN=replace-with-long-random-token
```

### 3. Endpoints

- `POST /api/prompts`
  - prompt登録
  - 例: `{ "prompt": "こんにちは" }`
- `GET /api/prompts/:id`
  - 状態と回答の確認
- `POST /api/jobs/claim`
  - ワーカーがジョブを取得
  - ヘッダ: `X-Worker-Token`
  - 例: `{ "timeout_seconds": 30, "lease_seconds": 60 }`
- `POST /api/jobs/:id/heartbeat`
  - 推論中のlease延長
  - ヘッダ: `X-Worker-Token`
  - 例: `{ "lease_token": "...", "lease_seconds": 60 }`
- `POST /api/jobs/:id/chunk`
  - 逐次生成の途中結果を追記
  - ヘッダ: `X-Worker-Token`
  - 例: `{ "lease_token": "...", "chunk": "途中の文字列" }`
- `POST /api/jobs/:id/result`
  - 回答返却
  - ヘッダ: `X-Worker-Token`, `Idempotency-Key`(任意)
  - 成功例: `{ "lease_token": "...", "success": true, "response": "回答" }`
  - 失敗例: `{ "lease_token": "...", "success": false, "error": "timeout" }`

### 4. Python worker example

`script/lmstudio_worker.py` をそのまま実行してください。LM Studio から
ストリーミングで受けた出力を `chunk` エンドポイントに返し、最後に
`result` を送ります。

```bash
export RAILS_API_BASE=http://100.x.x.x:3000
export WORKER_SHARED_TOKEN=replace-with-long-random-token
export LMSTUDIO_BASE_URL=http://100.126.42.42:1234/v1
export LMSTUDIO_MODEL=your-model-name
python3 script/lmstudio_worker.py
```

