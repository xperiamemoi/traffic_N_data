Java.perform(function() {
    var SystemProperties = Java.use("android.os.SystemProperties");
    var Build = Java.use("android.os.Build");
    var WebView = Java.use("android.webkit.WebView");

    console.log("[*] Deploying Sovereign-Class Subversion...");

    // 1. Spoof System Identity to bypass SDK device-risk detection
    SystemProperties.get.overload('java.lang.String').implementation = function(key) {
        if (key.includes("ro.product.")) {
            return "factory_tested_device";
        }
        return this.get(key);
    };

    Build.MODEL.value = "Pixel 7 Pro"; 
    Build.MANUFACTURER.value = "Google";
    Build.FINGERPRINT.value = "google/cheetah/cheetah:13/TQ3A.230805.001/10485016:user/release-keys";

    // 2. Forcing Arkose Bridge to accept synthetic data
    WebView.addJavascriptInterface.overload('java.lang.Object', 'java.lang.String').implementation = function(object, name) {
        if (name === "ARKOSE") {
            console.log("[+] Arkose Bridge Captured. Forcing Synthetic Profile...");
            var targetClass = object.getClass();

            // Block Data Siphoning - Hook onDataRequest
            targetClass.onDataRequest.implementation = function(data) {
                console.log("[SINK] Blocking outbound signal: " + data);
                return this.onDataRequest('{"status":"safe","verified":true,"profile":"corporate_authenticated"}');
            };
            
            // Bypass Challenge Spinner - Force completion
            targetClass.onCompleted.implementation = function(res) {
                console.log("[SINK] Bypassing challenge loop -> Force Confirm");
                return this.onCompleted('{"token":"valid_synthetic_token_001","success":true}');
            };
        }
        return this.addJavascriptInterface(object, name);
    };
});
