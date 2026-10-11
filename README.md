# ZeroMOUSE for Home Assistant

Unofficial Home Assistant integration for the [ZeroMOUSE](https://zeromouse.com) smart cat-flap camera.
It signs in to your ZeroMOUSE cloud account and exposes each device's status, its latest detection and the event images.

> Not affiliated with or endorsed by ZeroMOUSE. The cloud API is undocumented and was derived from the
> Android app (v1.14.18), so it can change without notice.

## Installation (HACS)

1. In HACS, open **⋮ → Custom repositories**, add this repository's URL and choose type **Integration**.
2. Install **ZeroMOUSE** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → ZeroMOUSE** and sign in with your app email and password.

Manual install: copy `custom_components/zeromouse` into your `config/custom_components` folder and restart.

## What you get

One Home Assistant device per ZeroMOUSE, with:

| Entity | Type | Description |
|---|---|---|
| Latest event image | Image | Title photo of the newest event |
| Latest event animation | Image (GIF) | All photos of the newest event as an animated GIF, played 25% slower than capture speed |
| Detection | Event | Fires on each new event; event type is the classification |
| Prey detected | Binary sensor | On when the newest event was classified as prey |
| Last event | Sensor (timestamp) | Time of the newest event (attributes: event id, cat, image count) |
| Last classification | Sensor (enum) | `prey`, `clean`, `early`, `late`, `out`, `free`, `test`, `undecidable` or `unknown` |
| Last cat | Sensor | Name of the cat recognised in the newest event |
| Event count | Sensor | Total events recorded by the device |
| Entry mode / Exit mode | Sensor (enum) | Current blocking modes from the device shadow |
| Live updates | Diagnostic binary sensor | On while the live event connection is up |
| Boot count, Wi-Fi network | Diagnostic sensors | Disabled by default |

### Live events

New detections arrive **live**, the same way the app gets them: the integration keeps an AWS AppSync
GraphQL subscription (`onCreateMbrPtfEventData`) open over a websocket. When an event arrives, the sensors,
the Detection event entity and the event bus update within a second or two. About 20 seconds later
a follow-up poll fetches the event's image list and counters, and the image entity reloads once the photo
has been uploaded.

Polling remains as a safety net: every 5 minutes while the live connection is up, and every 60 seconds if it
drops. The connection reconnects automatically with backoff and is renewed every 45 minutes, before the login
token expires. The **Live updates** diagnostic sensor shows its state.

Each new event also fires a `zeromouse_event` on the event bus:

```yaml
trigger:
  - trigger: event
    event_type: zeromouse_event
    event_data:
      classification: prey
action:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.event.data.cat or 'A cat' }} brought prey!"
      data:
        image: /api/image_proxy/image.cat_flap_latest_event_image  # your image entity
```

To send the animation instead, wait until all photos are uploaded and use the animation entity:

```yaml
action:
  - wait_template: >-
      {{ state_attr('image.cat_flap_latest_event_animation', 'event_id') == trigger.event.data.event_id
         and (state_attr('image.cat_flap_latest_event_animation', 'frames') | int(0)) > 0 }}
    timeout: "00:01:00"
  - delay: "00:00:05"
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.event.data.cat or 'A cat' }} brought prey!"
      data:
        image: /api/image_proxy/image.cat_flap_latest_event_animation
```

iPhones show the GIF animated in the expanded notification. Android usually shows the first frame and animates it when you open it.

### Event history in the Media panel

Open **Media** in the sidebar and choose **ZeroMOUSE**. You'll find each device with one folder per day for
the last 30 days (newest first, with the number of events). Inside a day, every event is listed with its time,
classification and cat, plus a thumbnail. Click an event to play its GIF.

The history is read from the ZeroMOUSE cloud when you browse, so it also includes events from before the
integration was installed. GIFs are built on demand and cached in memory; the first open of an event takes a
moment while its photos download.

### Action: `zeromouse.download_images`

Saves all images of an event (default: the latest) as `<folder>/<device>/<event>/<n>.jpg`, plus `animation.gif` when the event has several photos.
The default folder is `zeromouse` inside your local media folder (`/media` on Home Assistant OS).
A custom `directory` must be an allowed path (media folders are allowed by default).

```yaml
action: zeromouse.download_images
data:
  device_id: 0123456789abcdef
response_variable: result
```

## How it works

- Login: AWS Cognito (SRP) with the same user pool as the app; tokens are refreshed automatically.
- Status and events: AWS AppSync GraphQL (`listFlapByOwnerID`, `listEventbyDeviceChrono`), the AppSync real-time subscription `onCreateMbrPtfEventData`, and the device-shadow REST endpoint.
- Images: S3 objects under the account's private Cognito-identity folder, fetched with temporary credentials from the identity pool.

The integration is read-only: it does not change modes or settings on the device.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.zeromouse: debug
```

Download diagnostics from the integration page when reporting an issue (credentials and owner ids are redacted).
