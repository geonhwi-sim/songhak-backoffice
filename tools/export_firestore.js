// 발주 앱(Firestore)의 컬렉션을 data/에 JSON으로 내려받는다. 개발·검증용.
// Firebase CLI에 로그인한 계정의 권한으로 읽으므로 관리자 전용 컬렉션(보관본·결제 정보)도 읽힌다.
// 운영 동기화는 4주차에 서비스 계정으로 바꾼다.
//
// 사용 (저장소 루트에서): node tools/export_firestore.js
// 결과: data/firestore_<컬렉션>_<YYYY-MM-DD>.json  (Firestore REST 응답의 documents 배열)

const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')

const PROJECT = 'songhak-inventory'
const BASE = `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents`
const COLLECTIONS = ['categories', 'items', 'requesters', 'orders', 'ordersArchive', 'orderPayments', 'paymentCards']
const OUT = path.join(__dirname, '..', 'data')

async function token() {
  const root = execSync('npm root -g').toString().trim()
  const auth = require(path.join(root, 'firebase-tools', 'lib', 'auth.js'))
  const account = auth.getGlobalDefaultAccount()
  if (!account) throw new Error('firebase login이 필요합니다')
  const t = await auth.getAccessToken(account.tokens.refresh_token, ['https://www.googleapis.com/auth/cloud-platform'])
  return t.access_token
}

async function listAll(tok, col) {
  const docs = []
  let pageToken = ''
  do {
    const url = `${BASE}/${col}?pageSize=300${pageToken ? `&pageToken=${pageToken}` : ''}`
    const res = await fetch(url, { headers: { Authorization: `Bearer ${tok}` } })
    const j = await res.json()
    if (j.error) throw new Error(`${col}: ${j.error.message}`)
    docs.push(...(j.documents || []))
    pageToken = j.nextPageToken || ''
  } while (pageToken)
  return docs
}

;(async () => {
  const tok = await token()
  const day = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10) // KST 날짜
  for (const col of COLLECTIONS) {
    const docs = await listAll(tok, col)
    fs.writeFileSync(path.join(OUT, `firestore_${col}_${day}.json`), JSON.stringify({ documents: docs }))
    console.log(`${col}: ${docs.length}건`)
  }
})().catch(e => { console.error('ERROR', e.message); process.exit(1) })
