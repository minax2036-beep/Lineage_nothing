# Nothing Image Unpacker

NothingArchiveのファームウェアイメージアーカイブ（`.7z`）を取得し、Androidの起動イメージを読み取り専用で解析するツールです。Nothing Phone (3a) Lite（`Galaxian`）を主な調査対象にしています。

## v0.3.0 の機能

- NothingArchiveから指定したHTTPS URLの`.7z`をダウンロード
- アーカイブ内の`.img`を抽出し、元のIMGを保存
- AOSP `unpack_bootimg.py`で`boot.img` / `init_boot.img` / `vendor_boot.img`を解析
- すべてのvendor ramdisk fragmentを検出して展開
- `init_boot.img`内のRamdiskを展開
- `boot.img`のKernelを検出し、Legacy LZ4 / LZ4 Frameなら`lz4`コマンドで展開を試行
- Kernelのサイズ、SHA-256、先頭バイト、`file`による形式情報を`summary.json`に記録
- `dtbo.img`や`vbmeta*.img`など、AOSP boot imageではないIMGも原本を保存して形式情報を記録
- SHA-256と処理結果を`summary.json`に記録

## Recovery調査用ファイルの自動収集

通常の全イメージ解析が終わると、GitHub Actionsは展開済みファイルを横断してRecovery / ADB / USB / fstab関連の候補を収集します。

- `init*.rc`、`ueventd*.rc`、`fstab*`などの設定ファイル
- `adbd` / `minadbd`などのADB実行ファイル
- USB ConfigFS、Recovery起動条件、ログ関連語を含むテキストファイル
- 元の配置パスを保ったコピーとSHA-256一覧

追加Artifact `nova-recovery-investigation-...` に、候補ファイルと `recovery-file-report.md` / `recovery-file-report.json` が保存されます。これは名前・内容による候補抽出であり、実行時に必ず読み込まれることを保証するものではありません。

## できないこと

- IMGから元のC/C++ソースコードを完全復元すること
- Kernelバイナリから元のKernelソースコードを復元すること
- Recoveryをビルドしたり、端末へ書き込んだりすること

このツールは読み取り・展開専用です。端末への書き込み、fastboot実行、パーティション変更は行いません。

## GitHub Actionsで実行

1. GitHubの`Actions`タブを開く
2. `Inspect Nothing firmware images`を選ぶ
3. `Run workflow`を押す
4. `archive_url`にNothingArchiveの`.7z`直接ダウンロードURLを入力
5. `build_label`にビルド名を入力して実行
6. 完了後、Artifactsから`nothing-image-inspected-...`を取得

入力URLは説明ページではなく、`.7z`ファイルそのもののHTTPSダウンロードURLにしてください。Artifactは7日で自動削除されます。

## Chromebook / Debianでローカル実行

```bash
sudo apt update
sudo apt install -y p7zip-full lz4 file python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tools/nothing-image-unpacker/requirements.txt
```

NothingArchiveの直接URLから実行:

```bash
python tools/nothing-image-unpacker/nothing_image_unpacker.py \
  --archive-url 'https://example.com/firmware-image-boot.7z' \
  --label 'Galaxian-B4.1-260819-1839' \
  --output out
```

ローカルにある`.7z`を使う場合:

```bash
python tools/nothing-image-unpacker/nothing_image_unpacker.py \
  --archive ./firmware-image-boot.7z \
  --label 'Galaxian-B4.1-260819-1839' \
  --output out
```

Recovery fragmentだけを取り出したい場合は、従来どおり`--recovery-only`を指定できます。

## 主な出力

```text
out/
└── Galaxian-B4.1-260819-1839/
    ├── images/
    │   ├── boot.img
    │   ├── init_boot.img
    │   ├── vendor_boot.img
    │   ├── dtbo.img
    │   └── vbmeta*.img
    ├── unpacked/
    │   ├── boot/
    │   │   ├── kernel
    │   │   ├── kernel-decompressed
    │   │   └── image-info.txt
    │   ├── init_boot/
    │   │   ├── ramdisk
    │   │   └── ramdisk_root/
    │   └── vendor_boot/
    │       ├── dtb
    │       ├── vendor_ramdisk00
    │       ├── vendor_ramdisk01
    │       └── vendor-ramdisk-by-name/
    └── summary.json
```

実際のファイル名やfragment数はファームウェアによって異なります。Kernelの圧縮展開に失敗しても、元のKernelファイルは保持し、summaryに理由を記録します。

## 注意事項

- 必ず対象端末・地域・ビルドに合うアーカイブを選んでください。
- NothingArchiveの配布条件とファームウェアのライセンス・再配布条件に従ってください。
- 展開したファームウェア本体をGitリポジトリへコミットしないでください。GitHub Actions Artifactsなどで一時保管してください。
- AOSP `unpack_bootimg.py`は実行時にAndroid Open Source ProjectのGitilesから取得します。
