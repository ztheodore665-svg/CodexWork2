using System.Collections;
using PriestsAndDevils.Models;
using PriestsAndDevils.Views;
using UnityEngine;

namespace PriestsAndDevils.Controllers
{
    public sealed class GameController3D : MonoBehaviour
    {
        private GameModel model;
        private Game3DView view;
        private Coroutine timerCoroutine;
        private GameObject rectangleResource;
        private GameObject squareResource;
        private GameObject sphereResource;

        private void Awake()
        {
            Application.targetFrameRate = 60;
            Camera camera = Camera.main;
            camera.orthographic = false;
            camera.fieldOfView = 48;
            camera.transform.position = new Vector3(0, 11, -17);
            camera.transform.LookAt(new Vector3(0, 0, 0));
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.025f, 0.08f, 0.16f);
            LoadResources();
            model = new GameModel();
            model.Reset();
            view.Build(model);
        }

        private void LoadResources()
        {
            rectangleResource = CreatePrimitiveResource("RectanglePrefab", PrimitiveType.Cube, new Vector3(2.6f, 0.45f, 1.8f), new Color(0.36f, 0.14f, 0.045f));
            squareResource = CreatePrimitiveResource("SquarePrefab", PrimitiveType.Cube, Vector3.one, Color.white);
            sphereResource = CreatePrimitiveResource("SpherePrefab", PrimitiveType.Sphere, Vector3.one, new Color(0.92f, 0.08f, 0.1f));
            view = new Game3DView(this);
        }

        private GameObject CreatePrimitiveResource(string name, PrimitiveType type, Vector3 scale, Color color)
        {
            GameObject resource = GameObject.CreatePrimitive(type);
            resource.name = name;
            resource.transform.localScale = scale;
            resource.GetComponent<Renderer>().material.color = color;
            resource.SetActive(false);
            return resource;
        }

        private void Start()
        {
            timerCoroutine = StartCoroutine(TimerLoop());
        }

        public void OnPersonClicked(int personId)
        {
            model.TryTogglePassenger(personId, out string message);
            view.Refresh(model);
            if (!string.IsNullOrEmpty(message)) Debug.Log(message);
        }

        public void OnGoClicked()
        {
            if (!model.CanMove(out string message))
            {
                Debug.LogWarning(message);
                view.Refresh(model);
                return;
            }
            StartCoroutine(MoveBoatRoutine());
        }

        public void OnRestartClicked()
        {
            if (timerCoroutine != null) StopCoroutine(timerCoroutine);
            model.Reset();
            view.Refresh(model);
            timerCoroutine = StartCoroutine(TimerLoop());
        }

        private IEnumerator MoveBoatRoutine()
        {
            model.Boat.IsMoving = true;
            view.Refresh(model);
            Vector3 start = model.Boat.Side == Side.Left ? new Vector3(-1.9f, 0.55f, 0) : new Vector3(1.9f, 0.55f, 0);
            Vector3 end = model.Boat.Side == Side.Left ? new Vector3(1.9f, 0.55f, 0) : new Vector3(-1.9f, 0.55f, 0);
            float elapsed = 0;
            while (elapsed < 1.5f)
            {
                elapsed += Time.deltaTime;
                view.SetBoatPosition(Vector3.Lerp(start, end, elapsed / 1.5f));
                yield return null;
            }
            model.MoveBoat();
            model.Boat.IsMoving = false;
            view.Refresh(model);
        }

        private IEnumerator TimerLoop()
        {
            while (model.Status == GameStatus.Playing)
            {
                yield return new WaitForSeconds(1f);
                model.TickSecond();
                view.Refresh(model);
            }
        }
    }
}
