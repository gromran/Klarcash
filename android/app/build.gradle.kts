plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "de.hauptbuch.ausgaben"
    compileSdk = 34

    defaultConfig {
        applicationId = "de.hauptbuch.ausgaben"
        // MediaStore.Downloads (used for the CSV export, see MainActivity.kt)
        // needs API 29+; kept as the floor here to avoid a legacy
        // storage-permission fallback path for what is a personal-use app.
        minSdk = 29
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        ndk {
            // Chaquopy bundles a full CPython build per ABI (~15-25+ MB each).
            // arm64-v8a alone covers virtually all devices sold in the last
            // several years; add "armeabi-v7a" here if older 32-bit devices
            // must be supported, at roughly double the APK size.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // Chaquopy needs a local CPython of exactly this version to resolve the
        // pip requirements below. Python 3.13 is installed here, but it's not on
        // PATH and the py launcher defaults to 3.14, so point at it directly.
        buildPython("C:/Users/Gromran/AppData/Local/Programs/Python/Python313/python.exe")
        pip {
            install("flask==3.0.3")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
        }
    }
}

// The Flask app (app.py, db.py, templates/, static/) lives one level up in
// the main project root and stays the single source of truth. This task
// copies exactly those paths into Chaquopy's python source set before every
// build, so tests/, .venv/, ausgaben.db, .idea/ etc. never end up in the APK
// and the Android build never drifts from the desktop app.
tasks.register<Copy>("copyPythonSources") {
    val projectRoot = rootProject.projectDir.parentFile!!
    from(projectRoot) {
        include("app.py", "db.py")
    }
    from(projectRoot.resolve("templates")) {
        into("templates")
    }
    from(projectRoot.resolve("static")) {
        into("static")
    }
    into("src/main/python")
}

// Chaquopy's merge<Variant>PythonSources tasks read src/main/python directly.
// A preBuild dependency alone doesn't tell Gradle that, so it flags the copy
// as an implicit dependency and may order the tasks wrongly. Wire the
// dependency explicitly on every variant's merge task instead.
tasks.matching { it.name.matches(Regex("merge.*PythonSources")) }.configureEach {
    dependsOn("copyPythonSources")
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
