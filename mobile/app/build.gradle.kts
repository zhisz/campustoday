plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val releaseKeystore = System.getenv("CAMPUS_KEYSTORE_PATH")
val releasePassword = System.getenv("CAMPUS_KEYSTORE_PASSWORD")

android {
    namespace = "xyz.zhisz.campustoday"
    compileSdk = 35

    defaultConfig {
        applicationId = "xyz.zhisz.campustoday"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "1.0.2"
        buildConfigField("String", "API_BASE_URL", "\"https://campustoday.zhisz.xyz\"")
    }

    signingConfigs {
        if (!releaseKeystore.isNullOrBlank() && !releasePassword.isNullOrBlank()) create("release") {
            storeFile = file(releaseKeystore)
            storePassword = releasePassword
            keyAlias = "campustoday"
            keyPassword = releasePassword
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (signingConfigs.names.contains("release")) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.05.01")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
