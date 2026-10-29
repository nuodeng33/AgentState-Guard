plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val acceptanceStore = providers.environmentVariable("ASG_ACCEPTANCE_KEYSTORE_PATH").orNull
val acceptancePassword = providers.environmentVariable("ASG_ACCEPTANCE_STORE_PASSWORD").orNull
val hasAcceptanceSigning = !acceptanceStore.isNullOrBlank() && !acceptancePassword.isNullOrBlank()

android {
    namespace = "com.agentstate.guard"
    compileSdk = 34
    testBuildType = if (providers.gradleProperty("asgAcceptanceInstrumentation").orNull == "true") "acceptance" else "debug"

    defaultConfig {
        applicationId = "com.agentstate.guard"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.9.0-dev"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("asgAcceptance") {
            storeFile = acceptanceStore?.let { file(it) }
            storePassword = acceptancePassword
            keyAlias = "asg-acceptance"
            keyPassword = acceptancePassword
        }
    }
    buildTypes {
        getByName("debug") {
            if (hasAcceptanceSigning) signingConfig = signingConfigs.getByName("asgAcceptance")
        }
        create("acceptance") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".acceptance"
            versionNameSuffix = "-acceptance"
            signingConfig = signingConfigs.getByName("asgAcceptance")
            matchingFallbacks += listOf("debug")
        }
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.05.00"))
    implementation("androidx.compose.material3:material3")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.navigation:navigation-compose:2.7.7")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20230227")
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
}

// Local debug/unit work remains usable without distribution credentials.
// Installable acceptance artifacts must never fall back to an ephemeral key.
tasks.matching { it.name == "validateSigningAcceptance" }.configureEach {
    doFirst {
        check(hasAcceptanceSigning) { "ASG acceptance signing inputs are required; ephemeral signing is forbidden." }
    }
}
