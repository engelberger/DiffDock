import math

from e3nn import o3
import torch
from torch import nn
from torch.nn import functional as F
from torch_cluster import radius, radius_graph
from torch_scatter import scatter, scatter_mean
import numpy as np
from e3nn.nn import BatchNorm

from utils import so3, torus
from datasets.process_mols import lig_feature_dims, rec_residue_feature_dims
 
"""
    Here they define a PyTorch module called AtomEncoder. This module is used to encode the atomic features of a 
    molecule into a vector representation. The atomic features can be either categorical (e.g. element type) or scalar 
    (e.g. partial charge). The module takes as input the number of dimensions of the embedding (emb_dim), the dimensions 
    of the atomic features (feature_dims), and the number of dimensions for a type of molecular representation called sigma_embed_dim.

    The module uses an nn.ModuleList to store multiple nn.Embedding modules, one for each categorical feature. 
    Each nn.Embedding module maps an integer representing the category to an embedding vector. If the molecule 
    has any scalar features, these are concatenated to the embedding vectors from the categorical features and 
    passed through a nn.Linear module to get a final embedding vector.

    The TensorProductConvLayer module is a type of neural network layer that operates on graphs. The layer takes 
    as input a graph, with nodes representing atoms in the molecule, and edges representing bonds between atoms. 
    The nodes of the graph are assigned features, which in this case are the embeddings from the AtomEncoder module. 
    The layer operates on the graph by performing a tensor product on the node features, which allows for the 
    capture of relationships between multiple nodes.

    The layer is initialized with the number of input irreps (in_irreps), number of spherical harmonics irreps (sh_irreps), 
    number of output irreps (out_irreps), and the number of features on the edges of the graph (n_edge_features). 
    The layer also takes in optional parameters like residual (whether to add the input features back to the output features), 
    batch_norm (whether to apply batch normalization), dropout (amount of dropout to apply), and hidden_features 
    (number of hidden features in the fully connected layers).

    The layer contains a tp (tensor product) module from the e3nn library, which performs the tensor product operation. 
    The edge features are passed through a fully connected neural network to get the weights for the tensor product 
    operation. Finally, the output features from the tensor product operation are batch normalized if batch_norm is 
    set to True, and the input features are added to the output features if residual is set to True.

    The second section of the TensorProductScoreModel class defines the attributes and parameters of the neural network model used for scoring the protein-ligand interactions. These attributes and parameters are then used to create different parts of the model in the following sections.

    The constructor __init__ takes several arguments and sets them as instance variables of the class, including the t_to_sigma which represents the mapping from a timestep to a sigmas value, device which specifies the device to run the computations on, timestep_emb_func which is a function to embed the timestep, in_lig_edge_features which represents the number of input features for the ligand edge embedding layer, sigma_embed_dim which is the dimension of the sigma embedding, sh_lmax which represents the maximum spherical harmonics order, ns and nv which are the number of nodes in the ligand and receptor graph respectively, num_conv_layers which is the number of convolution layers to use, lig_max_radius which is the maximum radius of the ligand, rec_max_radius which is the maximum radius of the receptor, cross_max_distance which is the maximum distance between the ligand and receptor, center_max_distance which is the maximum distance of the center of the ligand to the receptor, distance_embed_dim which is the embedding dimension of the distance, cross_distance_embed_dim which is the embedding dimension of the cross distance, no_torsion which specifies whether to use torsion angles or not, scale_by_sigma which specifies whether to scale by sigma or not, use_second_order_repr which specifies whether to use a second-order representation or not, batch_norm which specifies whether to use batch normalization or not, dynamic_max_cross which specifies whether to use dynamic maximum cross distances or not, dropout which is the dropout rate to use, lm_embedding_type which is the type of the light molecule embedding, confidence_mode which specifies whether to use confidence mode or not, confidence_dropout which is the confidence dropout rate to use, confidence_no_batchnorm which specifies whether to use batch normalization in confidence mode or not, and num_confidence_outputs which is the number of confidence outputs.

    The instance variables are then used to create different parts of the model, including:

        lig_node_embedding: the ligand node embedding layer, which maps the features of the ligand nodes to a node-wise representation.
        lig_edge_embedding: the ligand edge embedding layer, which maps the features of the ligand edges to a edge-wise representation.
        rec_node_embedding: the receptor node embedding layer, which maps the features of the receptor nodes to a node-wise representation.
        rec_edge_embedding: the receptor edge embedding layer, which maps the features of the receptor edges to a edge-wise representation.
        cross_edge_embedding: the cross edge embedding layer, which maps the features of the edges connecting the ligand and receptor nodes to a edge-wise representation.
        lig_distance_expansion: the ligand distance expansion layer, which maps distances between the ligand nodes to a distance


        First, the init method of the TensorProductScoreModel class is defined. This method is called automatically when an instance of the class is created. It takes several arguments that define the properties and behavior of the neural network. The arguments include:

        t_to_sigma: a tensor that maps timesteps to molecular scales
        device: the device to use for computations (e.g. CPU or GPU)
        timestep_emb_func: a function that maps timesteps to embeddings
        in_lig_edge_features: the number of input features for the ligand edge embedding
        sigma_embed_dim: the dimensionality of the molecular scale embeddings
        sh_lmax: the maximum spherical harmonic degree
        ns: the number of atoms in the node embeddings
        nv: the number of timestep embeddings
        num_conv_layers: the number of convolutional layers to use
        lig_max_radius: the maximum radius for the ligand distance expansion
        rec_max_radius: the maximum radius for the receptor distance expansion
        cross_max_distance: the maximum distance for the cross-molecular expansion
        center_max_distance: the maximum distance from the center for computing distances
        distance_embed_dim: the dimensionality of the distance embeddings
        cross_distance_embed_dim: the dimensionality of the cross-molecular distance embeddings
        no_torsion: a flag indicating whether to use torsion information
        scale_by_sigma: a flag indicating whether to scale by molecular scale
        use_second_order_repr: a flag indicating whether to use second-order representations
        batch_norm: a flag indicating whether to use batch normalization
        dynamic_max_cross: a flag indicating whether to dynamically set the maximum cross-molecular distance
        dropout: the dropout rate to use for regularization
        lm_embedding_type: the type of local coordinate system to use for the receptor node embedding
        confidence_mode: a flag indicating whether to compute confidence scores
        confidence_dropout: the dropout rate to use for the confidence scores
        confidence_no_batchnorm: a flag indicating whether to use batch normalization for the confidence scores
        num_confidence_outputs: the number of confidence scores to compute

        The first line of the init method calls the init method of the parent class, torch.nn.Module, to properly initialize the class.

        Next, the arguments passed to the init method are stored as class attributes so that they can be used elsewhere in the class. This is done by simply assigning the arguments to the corresponding attributes, e.g. self.device = device.
"""


class AtomEncoder(torch.nn.Module):

    def __init__(self, emb_dim, feature_dims, sigma_embed_dim, lm_embedding_type= None):
        # first element of feature_dims tuple is a list with the lenght of each categorical feature and the second is the number of scalar features
        super(AtomEncoder, self).__init__()
        self.atom_embedding_list = torch.nn.ModuleList()
        self.num_categorical_features = len(feature_dims[0])
        self.num_scalar_features = feature_dims[1] + sigma_embed_dim
        self.lm_embedding_type = lm_embedding_type
        for i, dim in enumerate(feature_dims[0]):
            emb = torch.nn.Embedding(dim, emb_dim)
            torch.nn.init.xavier_uniform_(emb.weight.data)
            self.atom_embedding_list.append(emb)

        if self.num_scalar_features > 0:
            self.linear = torch.nn.Linear(self.num_scalar_features, emb_dim)
        if self.lm_embedding_type is not None:
            if self.lm_embedding_type == 'esm':
                self.lm_embedding_dim = 1280
            else: raise ValueError('LM Embedding type was not correctly determined. LM embedding type: ', self.lm_embedding_type)
            self.lm_embedding_layer = torch.nn.Linear(self.lm_embedding_dim + emb_dim, emb_dim)

    def forward(self, x):
        x_embedding = 0
        if self.lm_embedding_type is not None:
            assert x.shape[1] == self.num_categorical_features + self.num_scalar_features + self.lm_embedding_dim
        else:
            assert x.shape[1] == self.num_categorical_features + self.num_scalar_features
        for i in range(self.num_categorical_features):
            x_embedding += self.atom_embedding_list[i](x[:, i].long())

        if self.num_scalar_features > 0:
            x_embedding += self.linear(x[:, self.num_categorical_features:self.num_categorical_features + self.num_scalar_features])
        if self.lm_embedding_type is not None:
            x_embedding = self.lm_embedding_layer(torch.cat([x_embedding, x[:, -self.lm_embedding_dim:]], axis=1))
        return x_embedding


class TensorProductConvLayer(torch.nn.Module):
    def __init__(self, in_irreps, sh_irreps, out_irreps, n_edge_features, residual=True, batch_norm=True, dropout=0.0,
                 hidden_features=None):
        super(TensorProductConvLayer, self).__init__()
        self.in_irreps = in_irreps
        self.out_irreps = out_irreps
        self.sh_irreps = sh_irreps
        self.residual = residual
        if hidden_features is None:
            hidden_features = n_edge_features

        self.tp = tp = o3.FullyConnectedTensorProduct(in_irreps, sh_irreps, out_irreps, shared_weights=False)

        self.fc = nn.Sequential(
            nn.Linear(n_edge_features, hidden_features),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_features, tp.weight_numel)
        )
        self.batch_norm = BatchNorm(out_irreps) if batch_norm else None

    def forward(self, node_attr, edge_index, edge_attr, edge_sh, out_nodes=None, reduce='mean'):

        edge_src, edge_dst = edge_index
        tp = self.tp(node_attr[edge_dst], edge_sh, self.fc(edge_attr))

        out_nodes = out_nodes or node_attr.shape[0]
        out = scatter(tp, edge_src, dim=0, dim_size=out_nodes, reduce=reduce)

        if self.residual:
            padded = F.pad(node_attr, (0, out.shape[-1] - node_attr.shape[-1]))
            out = out + padded

        if self.batch_norm:
            out = self.batch_norm(out)
        return out


class TensorProductScoreModel(torch.nn.Module):
    def __init__(self, t_to_sigma, device, timestep_emb_func, in_lig_edge_features=4, sigma_embed_dim=32, sh_lmax=2,
                 ns=16, nv=4, num_conv_layers=2, lig_max_radius=5, rec_max_radius=30, cross_max_distance=250,
                 center_max_distance=30, distance_embed_dim=32, cross_distance_embed_dim=32, no_torsion=False,
                 scale_by_sigma=True, use_second_order_repr=False, batch_norm=True,
                 dynamic_max_cross=False, dropout=0.0, lm_embedding_type=None, confidence_mode=False,
                 confidence_dropout=0, confidence_no_batchnorm=False, num_confidence_outputs=1):
        super(TensorProductScoreModel, self).__init__()
        self.t_to_sigma = t_to_sigma
        self.in_lig_edge_features = in_lig_edge_features
        self.sigma_embed_dim = sigma_embed_dim
        self.lig_max_radius = lig_max_radius
        self.rec_max_radius = rec_max_radius
        self.cross_max_distance = cross_max_distance
        self.dynamic_max_cross = dynamic_max_cross
        self.center_max_distance = center_max_distance
        self.distance_embed_dim = distance_embed_dim
        self.cross_distance_embed_dim = cross_distance_embed_dim
        self.sh_irreps = o3.Irreps.spherical_harmonics(lmax=sh_lmax)
        self.ns, self.nv = ns, nv
        self.scale_by_sigma = scale_by_sigma
        self.device = device
        self.no_torsion = no_torsion
        self.timestep_emb_func = timestep_emb_func
        self.confidence_mode = confidence_mode
        self.num_conv_layers = num_conv_layers

        self.lig_node_embedding = AtomEncoder(emb_dim=ns, feature_dims=lig_feature_dims, sigma_embed_dim=sigma_embed_dim)
        self.lig_edge_embedding = nn.Sequential(nn.Linear(in_lig_edge_features + sigma_embed_dim + distance_embed_dim, ns),nn.ReLU(), nn.Dropout(dropout),nn.Linear(ns, ns))

        self.rec_node_embedding = AtomEncoder(emb_dim=ns, feature_dims=rec_residue_feature_dims, sigma_embed_dim=sigma_embed_dim, lm_embedding_type=lm_embedding_type)
        self.rec_edge_embedding = nn.Sequential(nn.Linear(sigma_embed_dim + distance_embed_dim, ns), nn.ReLU(), nn.Dropout(dropout),nn.Linear(ns, ns))

        self.cross_edge_embedding = nn.Sequential(nn.Linear(sigma_embed_dim + cross_distance_embed_dim, ns), nn.ReLU(), nn.Dropout(dropout),nn.Linear(ns, ns))

        self.lig_distance_expansion = GaussianSmearing(0.0, lig_max_radius, distance_embed_dim)
        self.rec_distance_expansion = GaussianSmearing(0.0, rec_max_radius, distance_embed_dim)
        self.cross_distance_expansion = GaussianSmearing(0.0, cross_max_distance, cross_distance_embed_dim)

        if use_second_order_repr:
            irrep_seq = [
                f'{ns}x0e',
                f'{ns}x0e + {nv}x1o + {nv}x2e',
                f'{ns}x0e + {nv}x1o + {nv}x2e + {nv}x1e + {nv}x2o',
                f'{ns}x0e + {nv}x1o + {nv}x2e + {nv}x1e + {nv}x2o + {ns}x0o'
            ]
        else:
            irrep_seq = [
                f'{ns}x0e',
                f'{ns}x0e + {nv}x1o',
                f'{ns}x0e + {nv}x1o + {nv}x1e',
                f'{ns}x0e + {nv}x1o + {nv}x1e + {ns}x0o'
            ]

        lig_conv_layers, rec_conv_layers, lig_to_rec_conv_layers, rec_to_lig_conv_layers = [], [], [], []
        for i in range(num_conv_layers):
            in_irreps = irrep_seq[min(i, len(irrep_seq) - 1)]
            out_irreps = irrep_seq[min(i + 1, len(irrep_seq) - 1)]
            parameters = {
                'in_irreps': in_irreps,
                'sh_irreps': self.sh_irreps,
                'out_irreps': out_irreps,
                'n_edge_features': 3 * ns,
                'hidden_features': 3 * ns,
                'residual': False,
                'batch_norm': batch_norm,
                'dropout': dropout
            }

            lig_layer = TensorProductConvLayer(**parameters)
            lig_conv_layers.append(lig_layer)
            rec_layer = TensorProductConvLayer(**parameters)
            rec_conv_layers.append(rec_layer)
            lig_to_rec_layer = TensorProductConvLayer(**parameters)
            lig_to_rec_conv_layers.append(lig_to_rec_layer)
            rec_to_lig_layer = TensorProductConvLayer(**parameters)
            rec_to_lig_conv_layers.append(rec_to_lig_layer)

        self.lig_conv_layers = nn.ModuleList(lig_conv_layers)
        self.rec_conv_layers = nn.ModuleList(rec_conv_layers)
        self.lig_to_rec_conv_layers = nn.ModuleList(lig_to_rec_conv_layers)
        self.rec_to_lig_conv_layers = nn.ModuleList(rec_to_lig_conv_layers)

        if self.confidence_mode:
            self.confidence_predictor = nn.Sequential(
                nn.Linear(2*self.ns if num_conv_layers >= 3 else self.ns,ns),
                nn.BatchNorm1d(ns) if not confidence_no_batchnorm else nn.Identity(),
                nn.ReLU(),
                nn.Dropout(confidence_dropout),
                nn.Linear(ns, ns),
                nn.BatchNorm1d(ns) if not confidence_no_batchnorm else nn.Identity(),
                nn.ReLU(),
                nn.Dropout(confidence_dropout),
                nn.Linear(ns, num_confidence_outputs)
            )
        else:
            # center of mass translation and rotation components
            self.center_distance_expansion = GaussianSmearing(0.0, center_max_distance, distance_embed_dim)
            self.center_edge_embedding = nn.Sequential(
                nn.Linear(distance_embed_dim + sigma_embed_dim, ns),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(ns, ns)
            )

            self.final_conv = TensorProductConvLayer(
                in_irreps=self.lig_conv_layers[-1].out_irreps,
                sh_irreps=self.sh_irreps,
                out_irreps=f'2x1o + 2x1e',
                n_edge_features=2 * ns,
                residual=False,
                dropout=dropout,
                batch_norm=batch_norm
            )
            self.tr_final_layer = nn.Sequential(nn.Linear(1 + sigma_embed_dim, ns),nn.Dropout(dropout), nn.ReLU(), nn.Linear(ns, 1))
            self.rot_final_layer = nn.Sequential(nn.Linear(1 + sigma_embed_dim, ns),nn.Dropout(dropout), nn.ReLU(), nn.Linear(ns, 1))

            if not no_torsion:
                # torsion angles components
                self.final_edge_embedding = nn.Sequential(
                    nn.Linear(distance_embed_dim, ns),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                    nn.Linear(ns, ns)
                )
                self.final_tp_tor = o3.FullTensorProduct(self.sh_irreps, "2e")
                self.tor_bond_conv = TensorProductConvLayer(
                    in_irreps=self.lig_conv_layers[-1].out_irreps,
                    sh_irreps=self.final_tp_tor.irreps_out,
                    out_irreps=f'{ns}x0o + {ns}x0e',
                    n_edge_features=3 * ns,
                    residual=False,
                    dropout=dropout,
                    batch_norm=batch_norm
                )
                self.tor_final_layer = nn.Sequential(
                    nn.Linear(2 * ns, ns, bias=False),
                    nn.Tanh(),
                    nn.Dropout(dropout),
                    nn.Linear(ns, 1, bias=False)
                )

    def forward(self, data):
        if not self.confidence_mode:
            tr_sigma, rot_sigma, tor_sigma = self.t_to_sigma(*[data.complex_t[noise_type] for noise_type in ['tr', 'rot', 'tor']])
        else:
            tr_sigma, rot_sigma, tor_sigma = [data.complex_t[noise_type] for noise_type in ['tr', 'rot', 'tor']]

        # build ligand graph
        lig_node_attr, lig_edge_index, lig_edge_attr, lig_edge_sh = self.build_lig_conv_graph(data)
        lig_src, lig_dst = lig_edge_index
        lig_node_attr = self.lig_node_embedding(lig_node_attr)
        lig_edge_attr = self.lig_edge_embedding(lig_edge_attr)

        # build receptor graph
        rec_node_attr, rec_edge_index, rec_edge_attr, rec_edge_sh = self.build_rec_conv_graph(data)
        rec_src, rec_dst = rec_edge_index
        rec_node_attr = self.rec_node_embedding(rec_node_attr)
        rec_edge_attr = self.rec_edge_embedding(rec_edge_attr)

        # build cross graph
        if self.dynamic_max_cross:
            cross_cutoff = (tr_sigma * 3 + 20).unsqueeze(1)
        else:
            cross_cutoff = self.cross_max_distance
        cross_edge_index, cross_edge_attr, cross_edge_sh = self.build_cross_conv_graph(data, cross_cutoff)
        cross_lig, cross_rec = cross_edge_index
        cross_edge_attr = self.cross_edge_embedding(cross_edge_attr)

        for l in range(len(self.lig_conv_layers)):
            # intra graph message passing
            lig_edge_attr_ = torch.cat([lig_edge_attr, lig_node_attr[lig_src, :self.ns], lig_node_attr[lig_dst, :self.ns]], -1)
            lig_intra_update = self.lig_conv_layers[l](lig_node_attr, lig_edge_index, lig_edge_attr_, lig_edge_sh)

            # inter graph message passing
            rec_to_lig_edge_attr_ = torch.cat([cross_edge_attr, lig_node_attr[cross_lig, :self.ns], rec_node_attr[cross_rec, :self.ns]], -1)
            lig_inter_update = self.rec_to_lig_conv_layers[l](rec_node_attr, cross_edge_index, rec_to_lig_edge_attr_, cross_edge_sh,
                                                              out_nodes=lig_node_attr.shape[0])

            if l != len(self.lig_conv_layers) - 1:
                rec_edge_attr_ = torch.cat([rec_edge_attr, rec_node_attr[rec_src, :self.ns], rec_node_attr[rec_dst, :self.ns]], -1)
                rec_intra_update = self.rec_conv_layers[l](rec_node_attr, rec_edge_index, rec_edge_attr_, rec_edge_sh)

                lig_to_rec_edge_attr_ = torch.cat([cross_edge_attr, lig_node_attr[cross_lig, :self.ns], rec_node_attr[cross_rec, :self.ns]], -1)
                rec_inter_update = self.lig_to_rec_conv_layers[l](lig_node_attr, torch.flip(cross_edge_index, dims=[0]), lig_to_rec_edge_attr_,
                                                                  cross_edge_sh, out_nodes=rec_node_attr.shape[0])

            # padding original features
            lig_node_attr = F.pad(lig_node_attr, (0, lig_intra_update.shape[-1] - lig_node_attr.shape[-1]))

            # update features with residual updates
            lig_node_attr = lig_node_attr + lig_intra_update + lig_inter_update

            if l != len(self.lig_conv_layers) - 1:
                rec_node_attr = F.pad(rec_node_attr, (0, rec_intra_update.shape[-1] - rec_node_attr.shape[-1]))
                rec_node_attr = rec_node_attr + rec_intra_update + rec_inter_update

        # compute confidence score
        if self.confidence_mode:
            scalar_lig_attr = torch.cat([lig_node_attr[:,:self.ns],lig_node_attr[:,-self.ns:] ], dim=1) if self.num_conv_layers >= 3 else lig_node_attr[:,:self.ns]
            confidence = self.confidence_predictor(scatter_mean(scalar_lig_attr, data['ligand'].batch, dim=0)).squeeze(dim=-1)
            return confidence

        # compute translational and rotational score vectors
        center_edge_index, center_edge_attr, center_edge_sh = self.build_center_conv_graph(data)
        center_edge_attr = self.center_edge_embedding(center_edge_attr)
        center_edge_attr = torch.cat([center_edge_attr, lig_node_attr[center_edge_index[1], :self.ns]], -1)
        global_pred = self.final_conv(lig_node_attr, center_edge_index, center_edge_attr, center_edge_sh, out_nodes=data.num_graphs)

        tr_pred = global_pred[:, :3] + global_pred[:, 6:9]
        rot_pred = global_pred[:, 3:6] + global_pred[:, 9:]
        data.graph_sigma_emb = self.timestep_emb_func(data.complex_t['tr'])

        # fix the magnitude of translational and rotational score vectors
        tr_norm = torch.linalg.vector_norm(tr_pred, dim=1).unsqueeze(1)
        tr_pred = tr_pred / tr_norm * self.tr_final_layer(torch.cat([tr_norm, data.graph_sigma_emb], dim=1))
        rot_norm = torch.linalg.vector_norm(rot_pred, dim=1).unsqueeze(1)
        rot_pred = rot_pred / rot_norm * self.rot_final_layer(torch.cat([rot_norm, data.graph_sigma_emb], dim=1))

        if self.scale_by_sigma:
            tr_pred = tr_pred / tr_sigma.unsqueeze(1)
            rot_pred = rot_pred * so3.score_norm(rot_sigma.cpu()).unsqueeze(1).to(data['ligand'].x.device)

        if self.no_torsion or data['ligand'].edge_mask.sum() == 0: return tr_pred, rot_pred, torch.empty(0, device=self.device)

        # torsional components
        tor_bonds, tor_edge_index, tor_edge_attr, tor_edge_sh = self.build_bond_conv_graph(data)
        tor_bond_vec = data['ligand'].pos[tor_bonds[1]] - data['ligand'].pos[tor_bonds[0]]
        tor_bond_attr = lig_node_attr[tor_bonds[0]] + lig_node_attr[tor_bonds[1]]

        tor_bonds_sh = o3.spherical_harmonics("2e", tor_bond_vec, normalize=True, normalization='component')
        tor_edge_sh = self.final_tp_tor(tor_edge_sh, tor_bonds_sh[tor_edge_index[0]])

        tor_edge_attr = torch.cat([tor_edge_attr, lig_node_attr[tor_edge_index[1], :self.ns],
                                   tor_bond_attr[tor_edge_index[0], :self.ns]], -1)
        tor_pred = self.tor_bond_conv(lig_node_attr, tor_edge_index, tor_edge_attr, tor_edge_sh,
                                  out_nodes=data['ligand'].edge_mask.sum(), reduce='mean')
        tor_pred = self.tor_final_layer(tor_pred).squeeze(1)
        edge_sigma = tor_sigma[data['ligand'].batch][data['ligand', 'ligand'].edge_index[0]][data['ligand'].edge_mask]

        if self.scale_by_sigma:
            tor_pred = tor_pred * torch.sqrt(torch.tensor(torus.score_norm(edge_sigma.cpu().numpy())).float()
                                             .to(data['ligand'].x.device))
        return tr_pred, rot_pred, tor_pred

    def build_lig_conv_graph(self, data):
        # builds the ligand graph edges and initial node and edge features
        data['ligand'].node_sigma_emb = self.timestep_emb_func(data['ligand'].node_t['tr'])

        # compute edges
        radius_edges = radius_graph(data['ligand'].pos, self.lig_max_radius, data['ligand'].batch)
        edge_index = torch.cat([data['ligand', 'ligand'].edge_index, radius_edges], 1).long()
        edge_attr = torch.cat([
            data['ligand', 'ligand'].edge_attr,
            torch.zeros(radius_edges.shape[-1], self.in_lig_edge_features, device=data['ligand'].x.device)
        ], 0)

        # compute initial features
        edge_sigma_emb = data['ligand'].node_sigma_emb[edge_index[0].long()]
        edge_attr = torch.cat([edge_attr, edge_sigma_emb], 1)
        node_attr = torch.cat([data['ligand'].x, data['ligand'].node_sigma_emb], 1)

        src, dst = edge_index
        edge_vec = data['ligand'].pos[dst.long()] - data['ligand'].pos[src.long()]
        edge_length_emb = self.lig_distance_expansion(edge_vec.norm(dim=-1))

        edge_attr = torch.cat([edge_attr, edge_length_emb], 1)
        edge_sh = o3.spherical_harmonics(self.sh_irreps, edge_vec, normalize=True, normalization='component')

        return node_attr, edge_index, edge_attr, edge_sh

    def build_rec_conv_graph(self, data):
        # builds the receptor initial node and edge embeddings
        data['receptor'].node_sigma_emb = self.timestep_emb_func(data['receptor'].node_t['tr']) # tr rot and tor noise is all the same
        node_attr = torch.cat([data['receptor'].x, data['receptor'].node_sigma_emb], 1)

        # this assumes the edges were already created in preprocessing since protein's structure is fixed
        edge_index = data['receptor', 'receptor'].edge_index
        src, dst = edge_index
        edge_vec = data['receptor'].pos[dst.long()] - data['receptor'].pos[src.long()]

        edge_length_emb = self.rec_distance_expansion(edge_vec.norm(dim=-1))
        edge_sigma_emb = data['receptor'].node_sigma_emb[edge_index[0].long()]
        edge_attr = torch.cat([edge_sigma_emb, edge_length_emb], 1)
        edge_sh = o3.spherical_harmonics(self.sh_irreps, edge_vec, normalize=True, normalization='component')

        return node_attr, edge_index, edge_attr, edge_sh

    def build_cross_conv_graph(self, data, cross_distance_cutoff):
        # builds the cross edges between ligand and receptor
        if torch.is_tensor(cross_distance_cutoff):
            # different cutoff for every graph (depends on the diffusion time)
            edge_index = radius(data['receptor'].pos / cross_distance_cutoff[data['receptor'].batch],
                                data['ligand'].pos / cross_distance_cutoff[data['ligand'].batch], 1,
                                data['receptor'].batch, data['ligand'].batch, max_num_neighbors=10000)
        else:
            edge_index = radius(data['receptor'].pos, data['ligand'].pos, cross_distance_cutoff,
                            data['receptor'].batch, data['ligand'].batch, max_num_neighbors=10000)

        src, dst = edge_index
        edge_vec = data['receptor'].pos[dst.long()] - data['ligand'].pos[src.long()]

        edge_length_emb = self.cross_distance_expansion(edge_vec.norm(dim=-1))
        edge_sigma_emb = data['ligand'].node_sigma_emb[src.long()]
        edge_attr = torch.cat([edge_sigma_emb, edge_length_emb], 1)
        edge_sh = o3.spherical_harmonics(self.sh_irreps, edge_vec, normalize=True, normalization='component')

        return edge_index, edge_attr, edge_sh

    def build_center_conv_graph(self, data):
        # builds the filter and edges for the convolution generating translational and rotational scores
        edge_index = torch.cat([data['ligand'].batch.unsqueeze(0), torch.arange(len(data['ligand'].batch)).to(data['ligand'].x.device).unsqueeze(0)], dim=0)

        center_pos, count = torch.zeros((data.num_graphs, 3)).to(data['ligand'].x.device), torch.zeros((data.num_graphs, 3)).to(data['ligand'].x.device)
        center_pos.index_add_(0, index=data['ligand'].batch, source=data['ligand'].pos)
        center_pos = center_pos / torch.bincount(data['ligand'].batch).unsqueeze(1)

        edge_vec = data['ligand'].pos[edge_index[1]] - center_pos[edge_index[0]]
        edge_attr = self.center_distance_expansion(edge_vec.norm(dim=-1))
        edge_sigma_emb = data['ligand'].node_sigma_emb[edge_index[1].long()]
        edge_attr = torch.cat([edge_attr, edge_sigma_emb], 1)
        edge_sh = o3.spherical_harmonics(self.sh_irreps, edge_vec, normalize=True, normalization='component')
        return edge_index, edge_attr, edge_sh

    def build_bond_conv_graph(self, data):
        # builds the graph for the convolution between the center of the rotatable bonds and the neighbouring nodes
        bonds = data['ligand', 'ligand'].edge_index[:, data['ligand'].edge_mask].long()
        bond_pos = (data['ligand'].pos[bonds[0]] + data['ligand'].pos[bonds[1]]) / 2
        bond_batch = data['ligand'].batch[bonds[0]]
        edge_index = radius(data['ligand'].pos, bond_pos, self.lig_max_radius, batch_x=data['ligand'].batch, batch_y=bond_batch)

        edge_vec = data['ligand'].pos[edge_index[1]] - bond_pos[edge_index[0]]
        edge_attr = self.lig_distance_expansion(edge_vec.norm(dim=-1))

        edge_attr = self.final_edge_embedding(edge_attr)
        edge_sh = o3.spherical_harmonics(self.sh_irreps, edge_vec, normalize=True, normalization='component')

        return bonds, edge_index, edge_attr, edge_sh


class GaussianSmearing(torch.nn.Module):
    # used to embed the edge distances
    def __init__(self, start=0.0, stop=5.0, num_gaussians=50):
        super().__init__()
        offset = torch.linspace(start, stop, num_gaussians)
        self.coeff = -0.5 / (offset[1] - offset[0]).item() ** 2
        self.register_buffer('offset', offset)

    def forward(self, dist):
        dist = dist.view(-1, 1) - self.offset.view(1, -1)
        return torch.exp(self.coeff * torch.pow(dist, 2))
