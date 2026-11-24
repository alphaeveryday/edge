# source 해서 쓴다. Android 도구는 MOBILE_ENV(기본 /mnt/nvme/gitproject/mobile-env) 한 곳에 둔다.
export MOBILE_ENV=${MOBILE_ENV:-/mnt/nvme/gitproject/mobile-env}
export JAVA_HOME=$MOBILE_ENV/jdk
export ANDROID_HOME=$MOBILE_ENV/sdk ANDROID_SDK_ROOT=$MOBILE_ENV/sdk
export ANDROID_AVD_HOME=$MOBILE_ENV/avd ANDROID_USER_HOME=$MOBILE_ENV/android-user
export GRADLE_USER_HOME=$MOBILE_ENV/gradle
export TMPDIR=$MOBILE_ENV/tmp
export MAESTRO_CLI_NO_ANALYTICS=1 MAESTRO_CLI_ANALYSIS_NOTIFICATION_DISABLED=true
export PATH=$MOBILE_ENV/maestro/bin:$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH
export APP_ID=com.alphaeveryday.etforca AVD_NAME=orca35
mkdir -p "$TMPDIR" "$ANDROID_USER_HOME"
