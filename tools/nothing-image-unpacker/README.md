# Nothing Image Unpacker

NothingArchiveのパーティションイメージアーカイブ (`.7z`) を取得し、`.img` を取り出してAndroid boot image / vendor boot imageを解析するツールです。Nothing Phone (3a) Lite (`Galaxian`) を主な調査対象にしています。

NothingArchive: https://nothingarchive.tech/docs/firmware

## できること

- NothingArchiveから指定したHTTPS URLの`.7z`をダウンロード
- `.7z`内の`.img`を抽出して元ファイルを保存
- AOSP `unpack_bootimg.py`で`ANDROID!` / `VNDRBOOT`形式を展開
- Kernel、Ramdisk、DTB、bootconfig、ヘッダー情報などを分離
- 対応形式のRamdiskを展開し、`init*.rc`、`fstab*`、設定ファイルなどを取り出す
- GitHub ActionsではRecovery専用モードを使い、`vendor_boot.img`内のRecoveryタイプRamdiskだけを保存
- Legacy LZ4形式（magic `02 21 4c 18`）にも対応
- SHA-256と処理結果を`summary.json`に記録

## できないこと

`.img`はコンパイル済みバイナリを含むため、元のC/C++ソースコードを完全に復元することはできません。このツールが取り出すのは、Ramdisk内のスクリプト・設定ファイルと、Kernelなどのバイナリ部品です。Kernelのソースコードが必要な場合は、対応する公開カーネルソースを別途参照してください。

**このツールは読み取り・展開専用です。端末への書き込み、fastboot、パーティション変更は行いません。**

## GitHub Actionsで実行

1. GitHubの`Actions`タブを開く
2. `Unpack Nothing firmware images`を選ぶ
3. `Run workflow`を押す
4. NothingArchiveからコピーした`.7z`の直接ダウンロードURLを`archive_url`に入力
5. `build_label`にビルド名を入力して実行
6. 完了後、Artifactsから展開結果を取得

入力するURLは、ブラウザで開く説明ページではなく、`.7z`ファイルそのもののHTTPSダウンロードURLにしてください。

## Chromebook / Debianでローカル実行

```bash
sudo apt update
sudo apt install -y p7zip-full lz4 python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r tools/nothing-image-unpacker/requirements.txt
```

NothingArchiveの直接URLから実行:

```bash
python tools/nothing-image-unpacker/nothing_image_unpacker.py --archive-url 'https://example.com/firmware-image-boot.7z' --label 'Galaxian-B4.1' --recovery-only --output out
```

すでにダウンロード済みの`.7z`を使う場合:

```bash
python tools/nothing-image-unpacker/nothing_image_unpacker.py --archive ./firmware-image-boot.7z --label 'Galaxian-B4.1' --output out
```

## 出力構成

```text
out/
└── Galaxian-B4.1/
    ├── unpacked/               # Recovery専用Ramdisk
    │   └── vendor_boot/
    │       ├── image-info.txt  # AOSPのイメージヘッダー情報
    │       ├── kernel          # 存在する場合はバイナリ
    │       ├── ramdisk         # 圧縮Ramdisk
    │       └── ramdisk_root/   # 展開できた場合の設定・スクリプト
    └── summary.json            # SHA-256と抽出結果
```

Recovery専用モードでは`vendor_boot.img`だけを処理し、Recoveryと判定できたRamdiskだけを展開します。判定できない場合は別のRamdiskを推測で採用せず、summary.jsonに理由を記録します。

## 注意事項

- 必ず対象端末・地域・ビルドに合うアーカイブを選んでください。
- NothingArchiveの配布条件とファームウェアのライセンス・再配布条件に従ってください。
- 展開したファームウェア本体をGitリポジトリへコミットしないでください。GitHub ActionsのArtifactsなどで一時的に保管してください。
- AOSP `unpack_bootimg.py`は実行時にAndroid Open Source ProjectのGitilesから取得します。AOSPスクリプトはApache License 2.0で提供されています。
