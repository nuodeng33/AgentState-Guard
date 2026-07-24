plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.agentstate.guard"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.agentstate.guard"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.9.0-dev"
    }

    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
}
