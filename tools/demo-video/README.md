# 데모 영상 만들기

공개 데모 사이트를 자동으로 돌며 녹화한다. 커서와 설명 자막을 화면에 띄우고, Chrome이 보내는 화면 프레임(CDP screencast)을 모아 ffmpeg로 MP4를 만든다. 데모 질문을 1회 쓴다.

```bash
cd tools/demo-video
npm i --no-save playwright-core          # 브라우저는 내려받지 않고 설치된 Chrome을 쓴다 (경로: CHROME 환경 변수)
node record.mjs                          # frames/ 와 frames.json
node encode.mjs ../../docs/demo/demo.mp4 # ffmpeg 필요
```

화면이 바뀌면 다시 실행해 영상을 새로 만든다. README의 GIF는 MP4의 질문하기 구간을 잘라 만든다:

```bash
ffmpeg -ss 49.8 -t 21 -i ../../docs/demo/demo.mp4 -vf "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" ../../docs/demo/ask.gif
```
