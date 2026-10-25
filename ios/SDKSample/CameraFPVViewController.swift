//
//  CameraFPVViewController.swift
//  DJISDKSwiftDemo
//
//  Created by DJI on 2019/1/15.
//  Copyright © 2019 DJI. All rights reserved.
//

import UIKit
import DJISDK

class CameraFPVViewController: UIViewController {

    @IBOutlet weak var decodeModeSeg: UISegmentedControl!
    @IBOutlet weak var tempSwitch: UISwitch!
    @IBOutlet weak var tempLabel: UILabel!
    @IBOutlet weak var fpvView: UIView!

    var adapter: VideoPreviewerAdapter?
    var needToSetMode = false

    // AerialCopilot Relay UI controls
    fileprivate var relayIpField: UITextField?
    fileprivate var relayStatusBadge: UILabel?
    fileprivate var relayStreamBtn: UIButton?

    override func viewDidLoad() {
        super.viewDidLoad()

        let camera = fetchCamera()
        camera?.delegate = self

        needToSetMode = true

        DJIVideoPreviewer.instance()?.start()

        adapter = VideoPreviewerAdapter.init()
        adapter?.start()

        if camera?.displayName == DJICameraDisplayNameMavic2ZoomCamera ||
            camera?.displayName == DJICameraDisplayNameDJIMini2Camera ||
            camera?.displayName == DJICameraDisplayNameMavicAir2Camera ||
            camera?.displayName == DJICameraDisplayNameDJIAir2SCamera ||
            camera?.displayName == DJICameraDisplayNameMavic2ProCamera {
            adapter?.setupFrameControlHandler()
        }

        setupRelayUI()
    }


    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        DJIVideoPreviewer.instance()?.setView(fpvView)
        updateThermalCameraUI()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)

        // Stop streaming to Mac when leaving view
        if #available(iOS 13.0, *) {
            DJIVideoRelay.shared.stopStreaming()
        }

        // Call unSetView during exiting to release the memory.
        DJIVideoPreviewer.instance()?.unSetView()

        if adapter != nil {
            adapter?.stop()
            adapter = nil
        }
    }

    @IBAction func onSwitchValueChanged(_ sender: UISwitch) {
        guard let camera = fetchCamera() else { return }

        let mode: DJICameraThermalMeasurementMode = sender.isOn ? .spotMetering : .disabled
        camera.setThermalMeasurementMode(mode) { [weak self] (error) in
            if error != nil {
                self?.tempSwitch.setOn(false, animated: true)

                let alert = UIAlertController(title: nil, message: String(format: "Failed to set the measurement mode: %@", error?.localizedDescription ?? "unknown"), preferredStyle: .alert)

                alert.addAction(UIAlertAction(title: "ok", style: .cancel, handler: nil))

                self?.present(alert, animated: true)
            }
        }

    }

    /**
     *  DJIVideoPreviewer is used to decode the video data and display the decoded frame on the view. DJIVideoPreviewer provides both software
     *  decoding and hardware decoding. When using hardware decoding, for different products, the decoding protocols are different and the hardware decoding is only supported by some products.
     */
    @IBAction func onSegmentControlValueChanged(_ sender: UISegmentedControl) {
        DJIVideoPreviewer.instance()?.enableHardwareDecode = sender.selectedSegmentIndex == 1
    }

    fileprivate func updateThermalCameraUI() {
        guard let camera = fetchCamera(),
        camera.isThermalCamera()
        else {
            tempSwitch.setOn(false, animated: false)
            return
        }

        camera.getThermalMeasurementMode { [weak self] (mode, error) in
            if error != nil {
                let alert = UIAlertController(title: nil, message: String(format: "Failed to set the measurement mode: %@", error?.localizedDescription ?? "unknown"), preferredStyle: .alert)

                alert.addAction(UIAlertAction(title: "ok", style: .cancel, handler: nil))

                self?.present(alert, animated: true)

            } else {
                let enabled = mode != .disabled
                self?.tempSwitch.setOn(enabled, animated: true)

            }
        }
    }
}

/**
 *  DJICamera will send the live stream only when the mode is in DJICameraModeShootPhoto or DJICameraModeRecordVideo. Therefore, in order
 *  to demonstrate the FPV (first person view), we need to switch to mode to one of them.
 */
extension CameraFPVViewController: DJICameraDelegate {
    func camera(_ camera: DJICamera, didUpdate systemState: DJICameraSystemState) {
        if systemState.mode != .recordVideo && systemState.mode != .shootPhoto {
            return
        }
        if needToSetMode == false {
            return
        }
        needToSetMode = false
        self.setCameraMode(cameraMode: .shootPhoto)

    }

    func camera(_ camera: DJICamera, didUpdateTemperatureData temperature: Float) {
        tempLabel.text = String(format: "%f", temperature)
    }

}

extension CameraFPVViewController {
    fileprivate func fetchCamera() -> DJICamera? {
        guard let product = DJISDKManager.product() else {
            return nil
        }

        if product is DJIAircraft || product is DJIHandheld {
            return product.camera
        }
        return nil
    }

    fileprivate func setCameraMode(cameraMode: DJICameraMode = .shootPhoto) {
        var flatMode: DJIFlatCameraMode = .photoSingle
        let camera = self.fetchCamera()
        if camera?.isFlatCameraModeSupported() == true {
            NSLog("Flat camera mode detected")
            switch cameraMode {
            case .shootPhoto:
                flatMode = .photoSingle
            case .recordVideo:
                flatMode = .videoNormal
            default:
                flatMode = .photoSingle
            }
            camera?.setFlatMode(flatMode, withCompletion: { [weak self] (error: Error?) in
                if error != nil {
                    self?.needToSetMode = true
                    NSLog("Error set camera flat mode photo/video");
                }
            })
            } else {
                camera?.setMode(cameraMode, withCompletion: {[weak self] (error: Error?) in
                    if error != nil {
                        self?.needToSetMode = true
                        NSLog("Error set mode photo/video");
                    }
                })
            }
     }
}

// MARK: - AerialCopilot Relay UI & Streaming Service
extension CameraFPVViewController: UITextFieldDelegate {

    fileprivate func setupRelayUI() {
        guard #available(iOS 13.0, *) else { return }

        let container = UIView()
        container.translatesAutoresizingMaskIntoConstraints = false
        container.backgroundColor = UIColor(white: 0.1, alpha: 0.85)
        container.layer.cornerRadius = 12
        container.layer.borderWidth = 1
        container.layer.borderColor = UIColor(white: 0.3, alpha: 0.8).cgColor
        container.clipsToBounds = true
        view.addSubview(container)

        // Status Row
        let titleLabel = UILabel()
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.text = "🚁 AerialCopilot Mac Relay"
        titleLabel.font = UIFont.boldSystemFont(ofSize: 13)
        titleLabel.textColor = .white
        container.addSubview(titleLabel)

        let statusBadge = UILabel()
        statusBadge.translatesAutoresizingMaskIntoConstraints = false
        statusBadge.text = "Disconnected"
        statusBadge.font = UIFont.systemFont(ofSize: 12, weight: .semibold)
        statusBadge.textColor = .lightGray
        container.addSubview(statusBadge)

        // Controls Row
        let ipField = UITextField()
        ipField.translatesAutoresizingMaskIntoConstraints = false
        ipField.placeholder = "Mac IP (e.g. 10.134.42.174)"
        ipField.text = DJIVideoRelay.shared.targetIP
        ipField.borderStyle = .roundedRect
        ipField.font = UIFont.systemFont(ofSize: 13)
        ipField.backgroundColor = UIColor(white: 0.2, alpha: 0.9)
        ipField.textColor = .white
        ipField.keyboardType = .numbersAndPunctuation
        ipField.autocorrectionType = .no
        ipField.autocapitalizationType = .none
        ipField.delegate = self
        container.addSubview(ipField)

        let testBtn = UIButton(type: .system)
        testBtn.translatesAutoresizingMaskIntoConstraints = false
        testBtn.setTitle("Test Frame", for: .normal)
        testBtn.titleLabel?.font = UIFont.systemFont(ofSize: 12, weight: .semibold)
        testBtn.backgroundColor = UIColor.systemGray.withAlphaComponent(0.4)
        testBtn.setTitleColor(.white, for: .normal)
        testBtn.layer.cornerRadius = 6
        container.addSubview(testBtn)

        let streamBtn = UIButton(type: .system)
        streamBtn.translatesAutoresizingMaskIntoConstraints = false
        streamBtn.setTitle("Start Stream", for: .normal)
        streamBtn.titleLabel?.font = UIFont.systemFont(ofSize: 12, weight: .bold)
        streamBtn.backgroundColor = UIColor.systemBlue
        streamBtn.setTitleColor(.white, for: .normal)
        streamBtn.layer.cornerRadius = 6
        container.addSubview(streamBtn)

        // Layout constraints
        NSLayoutConstraint.activate([
            container.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12),
            container.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            container.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),

            titleLabel.topAnchor.constraint(equalTo: container.topAnchor, constant: 10),
            titleLabel.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 12),

            statusBadge.centerYAnchor.constraint(equalTo: titleLabel.centerYAnchor),
            statusBadge.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),

            ipField.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 8),
            ipField.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 12),
            ipField.heightAnchor.constraint(equalToConstant: 34),

            testBtn.centerYAnchor.constraint(equalTo: ipField.centerYAnchor),
            testBtn.leadingAnchor.constraint(equalTo: ipField.trailingAnchor, constant: 8),
            testBtn.widthAnchor.constraint(equalToConstant: 85),
            testBtn.heightAnchor.constraint(equalToConstant: 34),

            streamBtn.centerYAnchor.constraint(equalTo: ipField.centerYAnchor),
            streamBtn.leadingAnchor.constraint(equalTo: testBtn.trailingAnchor, constant: 8),
            streamBtn.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
            streamBtn.widthAnchor.constraint(equalToConstant: 95),
            streamBtn.heightAnchor.constraint(equalToConstant: 34),

            container.bottomAnchor.constraint(equalTo: ipField.bottomAnchor, constant: 10)
        ])

        // Tap gesture to dismiss keyboard
        let tapGesture = UITapGestureRecognizer(target: self, action: #selector(dismissKeyboard))
        tapGesture.cancelsTouchesInView = false
        view.addGestureRecognizer(tapGesture)

        self.relayIpField = ipField
        self.relayStatusBadge = statusBadge
        self.relayStreamBtn = streamBtn

        // Button actions via addTarget for universal iOS compatibility
        testBtn.addTarget(self, action: #selector(onRelayTestFrameTapped), for: .touchUpInside)
        streamBtn.addTarget(self, action: #selector(onRelayStreamButtonTapped), for: .touchUpInside)

        // Callbacks from relay
        DJIVideoRelay.shared.onStatusChanged = { [weak statusBadge, weak streamBtn] status, color in
            DispatchQueue.main.async {
                statusBadge?.text = status
                statusBadge?.textColor = color
                if !DJIVideoRelay.shared.isStreaming {
                    streamBtn?.setTitle("Start Stream", for: .normal)
                    streamBtn?.backgroundColor = .systemBlue
                }
            }
        }

        DJIVideoRelay.shared.onFPSUpdated = { [weak statusBadge] fps in
            DispatchQueue.main.async {
                statusBadge?.text = String(format: "Streaming (%.1f FPS)", fps)
                statusBadge?.textColor = .systemGreen
            }
        }
    }

    @objc fileprivate func dismissKeyboard() {
        view.endEditing(true)
    }

    @objc fileprivate func onRelayTestFrameTapped() {
        guard #available(iOS 13.0, *) else { return }
        view.endEditing(true)
        let ip = relayIpField?.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "10.134.42.174"
        relayStatusBadge?.text = "Sending frame..."
        relayStatusBadge?.textColor = .systemYellow

        DJIVideoRelay.shared.sendSingleFrame(ip: ip) { [weak self] success, message in
            DispatchQueue.main.async {
                self?.relayStatusBadge?.text = success ? "Test OK" : "Test Failed"
                self?.relayStatusBadge?.textColor = success ? .systemGreen : .systemRed

                let alert = UIAlertController(title: success ? "Frame Sent" : "Send Failed", message: message, preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "OK", style: .default, handler: nil))
                self?.present(alert, animated: true)
            }
        }
    }

    @objc fileprivate func onRelayStreamButtonTapped() {
        guard #available(iOS 13.0, *) else { return }
        view.endEditing(true)
        let relay = DJIVideoRelay.shared
        if relay.isStreaming {
            relay.stopStreaming()
            relayStreamBtn?.setTitle("Start Stream", for: .normal)
            relayStreamBtn?.backgroundColor = .systemBlue
        } else {
            let ip = relayIpField?.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "10.134.42.174"
            relay.startStreaming(ip: ip)
            relayStreamBtn?.setTitle("Stop Stream", for: .normal)
            relayStreamBtn?.backgroundColor = .systemRed
        }
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        textField.resignFirstResponder()
        return true
    }
}

// MARK: - DJIVideoRelay Streaming Service
