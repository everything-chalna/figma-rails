# README quick start (macOS)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.figma-sink-chrome" \
  --new-window "https://www.figma.com/design/AbCdEfGh123456/Example"
curl -s http://127.0.0.1:9222/json/version
curl -s http://127.0.0.1:9222/json/list
google-chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 --user-data-dir=/tmp/chrome-cdp-figma --no-first-run &
CHROME_PID=$!
kill "$CHROME_PID"
node cdp-eval.mjs --port 9222 --file-key AbCdEfGh123456 --probe
