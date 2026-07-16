import Foundation
import CoreLocation

class LocationManager: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    var locationFetched = false

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.requestAlwaysAuthorization()
        manager.startUpdatingLocation()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        if let location = locations.first {
            print("\(location.coordinate.latitude),\(location.coordinate.longitude)")
            locationFetched = true
            exit(0)
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        print("Error: \(error.localizedDescription)")
        exit(1)
    }
}

let delegate = LocationManager()
RunLoop.main.run(until: Date(timeIntervalSinceNow: 10))
if !delegate.locationFetched {
    print("Timeout")
    exit(1)
}
