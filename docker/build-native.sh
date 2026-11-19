#!/bin/bash
set -euo pipefail
trap 'status=$?; if [ "$status" -ne 0 ]; then for log in /release-sources/build/*.log; do echo "Build log: $log"; tail -n 45 "$log"; done; fi; exit "$status"' EXIT
export SOURCE_DATE_EPOCH=1794700800
export CMAKE_BUILD_PARALLEL_LEVEL=2
export PKG_CONFIG_PATH=/opt/zilcho-native/lib/pkgconfig
export LD_LIBRARY_PATH=/opt/zilcho-native/lib
mkdir -p /release-sources/native /release-sources/build /wheels /native-work
python /build/fetch_native_sources.py /build/native-lock.json /release-sources/native /native-work
cp /build/build-native.sh /release-sources/build/
dpkg-query -W > /release-sources/build/builder-debian-packages.txt
python -m pip freeze > /release-sources/build/builder-python-packages.txt
# Verify the downloaded signing key and release signature in an isolated build keyring.
mkdir -m 700 /native-work/gpg
export GNUPGHOME=/native-work/gpg
gpg --batch --import /release-sources/native/ffmpeg-devel.asc
fingerprint=$(gpg --batch --with-colons --fingerprint | awk -F: '$1=="fpr" {print $10; exit}')
test "$fingerprint" = FCF986EA15E6E293A5644F10B4322F04D67658D8
gpg --batch --verify /release-sources/native/ffmpeg-8.1.3.tar.xz.asc /release-sources/native/ffmpeg-8.1.3.tar.xz 2>&1 | tee /release-sources/build/ffmpeg-signature.log
cd /native-work/ffmpeg-8.1.3
# Decoding remains available; this app only encodes PCM audio for speech inference.
./configure --prefix=/opt/zilcho-native --disable-autodetect --disable-network --disable-doc --disable-debug --disable-static --enable-shared --disable-x86asm --disable-gpl --disable-nonfree --disable-encoders --enable-encoder=pcm_s16le --disable-muxers --enable-muxer=wav,matroska 2>&1 | tee /release-sources/build/ffmpeg-configure.log
make -j2 > /release-sources/build/ffmpeg-build.log 2>&1
make install >> /release-sources/build/ffmpeg-build.log 2>&1
cp ffbuild/config.log config.h config_components.h /release-sources/build/
cd /native-work/numpy-2.4.6
python -m pip wheel --no-deps --no-build-isolation . -w /wheels -Csetup-args=-Dblas=openblas -Csetup-args=-Dlapack=openblas -Ccompile-args=-j2 > /release-sources/build/numpy-build.log 2>&1
python -m pip install --no-deps /wheels/numpy-*.whl >> /release-sources/build/numpy-build.log 2>&1
cd /native-work/ctranslate2
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/zilcho-native -DWITH_MKL=OFF -DWITH_DNNL=OFF -DWITH_OPENBLAS=ON -DWITH_RUY=ON -DOPENMP_RUNTIME=COMP -DWITH_CUDA=OFF -DWITH_CUDNN=OFF -DBUILD_CLI=OFF -DBUILD_TESTS=OFF 2>&1 | tee /release-sources/build/ctranslate2-configure.log
cmake --build build -j2 > /release-sources/build/ctranslate2-build.log 2>&1
cmake --install build >> /release-sources/build/ctranslate2-build.log 2>&1
cp build/CMakeCache.txt /release-sources/build/
CTRANSLATE2_ROOT=/opt/zilcho-native python -m pip wheel --no-deps --no-build-isolation ./python -w /wheels > /release-sources/build/ctranslate2-python-build.log 2>&1
cd /native-work/av-18.1.0
python -m pip wheel --no-deps --no-build-isolation . -w /wheels > /release-sources/build/pyav-build.log 2>&1
sha256sum /wheels/*.whl > /release-sources/build/native-wheels-SHA256SUMS
